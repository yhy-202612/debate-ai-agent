"""事实核查节点：只对“疑似句”做模型核查，区分严重（剔除）与轻微（提示）。

- 预筛：先用正则挑出可能含具体数据/引用/过强断言的句子，仅这些交给模型，
  大幅减少核查调用与延迟；无候选则零成本返回。
- 宁缺勿错：严重（具体数字/引用/来源等无法当场核实）→ 从最终稿剔除；
  轻微（绝对化措辞）→ 仅提示并保留原句（避免误伤常识类比与举例）。
"""
from __future__ import annotations

import json
import re
from typing import Any

from .cost import UsageAcc
from .models import ChatClient, ModelError

SYSTEM = (
    "你是辩论稿的事实核查员。只判断句子是否依赖了当场无法核实的具体数据/引用/来源，"
    "或使用了过强的绝对化断言。你不评价观点对错，尤其不要因为‘举了一个常识性例子’而判为问题。"
)

PROMPT = (
    "对下列编号句子逐句核查，判断是否存在：\n"
    "severe（严重）：具体数字/百分比/金额/年份、机构名或研究报告等无来源背书、具体事件但无出处；\n"
    "mild（轻微）：‘所有/一切/任何/必然/一定/从来/每次/永远’等过强断言。\n"
    "常识性类比或举例（如‘汽车淘汰了马车夫’这类公认常识）不算问题，不要输出。\n"
    "只返回 JSON 数组，元素格式 {\"idx\":序号,\"level\":\"severe|mild\",\"problem\":\"问题\",\"suggest\":\"建议\"}；"
    "序号即给定句号；没有问题的句子不要输出；拿不准时，对具体数字一律判 severe。\n"
    "句子：{items}"
)

# 预筛触发词：命中才送模型核查
TRIGGER = re.compile(
    r"\d|[零一二三四五六七八九十百千万亿两]+(?:%|％|成|倍|年|项|亿|万|千)|"
    r"百分之|百分点|研究|数据|统计|报告|调查|机构|哈佛|斯坦福|联合国|世界卫生组织|"
    r"所有|一切|任何|必然|一定|从来|每次|永远|绝不|全都|都是"
)


def _candidates(sentences: list[str]) -> list[tuple[int, str]]:
    return [(i, s) for i, s in enumerate(sentences, 1) if TRIGGER.search(s or "")]


def _level(item: dict) -> str:
    lv = str(item.get("level") or "").strip().lower()
    if lv in ("severe", "mild"):
        return lv
    problem = str(item.get("problem") or "")
    if re.search(r"数字|数据|百分|来源|出处|引用|机构|报告|研究|统计|年份|金额|份额", problem):
        return "severe"
    return "mild"


def _parse_list(text: str) -> list[dict]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I).strip()
    data: Any = None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        a, b = raw.find("["), raw.rfind("]")
        if a >= 0 and b > a:
            try:
                data = json.loads(raw[a:b + 1])
            except json.JSONDecodeError:
                data = None
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("idx"))
        except (TypeError, ValueError):
            continue
        out.append({"idx": idx, "level": _level(item),
                    "problem": str(item.get("problem") or "").strip(),
                    "suggest": str(item.get("suggest") or "").strip()})
    return out


async def check(client: ChatClient, sentences: list[str],
                usage: UsageAcc | None = None) -> list[dict]:
    """核查句子列表，返回问题清单 [{idx, level, problem, suggest}]。失败返回空列表。"""
    sentences = [s for s in (sentences or []) if s and s.strip()]
    if not sentences:
        return []
    cand = _candidates(sentences)
    if not cand:
        return []
    items = [{"idx": i, "text": s} for i, s in cand]
    prompt = PROMPT.replace("{items}", json.dumps(items, ensure_ascii=False))
    try:
        text = await client.complete(SYSTEM, prompt, temperature=0.0,
                                     on_usage=(usage.add if usage else None))
    except ModelError:
        return []
    valid = {i for i, _ in cand}
    return [h for h in _parse_list(text) if h["idx"] in valid]
