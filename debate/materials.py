"""辩论队内资料库（赛前识别稿 / 总结稿 / 教练方法论）。

用途：把队内赛前讨论、质询训练、教练复盘提炼成可注入出稿提示词的「本队共识」，
以及一份可按需查阅的原始资料库。

存放位置：DEBATE_DATA_DIR/materials/*.md（默认 /data/materials，宿主持久化，容器重建不丢）。
注入策略：
  - 只把 00-共识摘要.md 注入提示词（信息密度最高、token 可控）；
  - 其余原文（各份总结稿、完整转写）只入库供 /api/materials 查阅，不自动注入。
开关：DEBATE_MATERIALS=on|off（默认 on）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

MAX_BRIEF_CHARS = 4600          # 2026-10-20：并入赛前备战长会定稿后加长（两级链路都注入足量）
BRIEF_NAME = "00-共识摘要.md"          # 默认（拓展 / 正方）
ERODE_BRIEF_NAME = "00-共识摘要-消解方.md"   # 反方训练版用；没有该文件则不注入


def materials_dir() -> Path:
    base = os.environ.get("DEBATE_DATA_DIR", "/data")
    d = Path(base) / "materials"
    d.mkdir(parents=True, exist_ok=True)
    return d


def enabled() -> bool:
    return os.environ.get("DEBATE_MATERIALS", "on").strip().lower() not in ("0", "off", "false", "no")


def _safe(name: str) -> str:
    n = os.path.basename(name or "").strip()
    return n if n.endswith(".md") else n + ".md"


def list_materials() -> list[dict]:
    out = []
    for p in sorted(materials_dir().glob("*.md")):
        try:
            st = p.stat()
        except OSError:
            continue
        out.append({"name": p.name, "bytes": st.st_size, "updated_at": int(st.st_mtime)})
    return out


def read_material(name: str, max_chars: int = 20000) -> str:
    p = materials_dir() / _safe(name)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="ignore")[:max_chars]


def save_material(name: str, text: str) -> dict:
    p = materials_dir() / _safe(name)
    p.write_text((text or "")[:200000], encoding="utf-8")
    return {"name": p.name, "bytes": p.stat().st_size}


def delete_material(name: str) -> bool:
    p = materials_dir() / _safe(name)
    if p.exists():
        p.unlink()
        return True
    return False


def _side_of(our_side: str) -> str:
    """判断我方是消解方还是拓展方。

    ★ 2026-10-20 队内定稿：**反方 = 拓展方，正方 = 消解方**（此前相反，已更正）。
      明确的立场词优先，队别只作兜底；不再用「发展」等泛词，避免「人工智能的发展」误判。
    """
    s = str(our_side or "")
    # ★最高优先：识别显式标记「我方立场=拓展/消解」。
    #   side_note() 生成的描述串里会同时出现「拓展」和「对方主张「消解」」，
    #   若只按关键词先后判断会被对方的立场词带偏（曾导致队内资料永不注入）。
    m = re.search(r"我方(?:立场)?\s*[=:：为]\s*(拓展|消解)", s)
    if m:
        return "expand" if m.group(1) == "拓展" else "erode"
    if any(k in s for k in ("消解", "侵蚀", "削弱", "取代", "否定")):
        return "erode"
    if any(k in s for k in ("拓展", "提升", "扩大")):
        return "expand"
    if "正方" in s:
        return "erode"      # 正方 = 消解方
    if "反方" in s:
        return "expand"     # 反方 = 拓展方
    return ""


def brief(our_side: str = "", max_chars: int = MAX_BRIEF_CHARS) -> str:
    """返回要注入提示词的「本队共识」摘要。

    ★ 按立场隔离：消解方（反方训练版）不会拿到拓展方的共识，避免立场串味；
      若没有对应的反方摘要文件，就不注入。
    """
    if not enabled():
        return ""
    side = _side_of(our_side)
    name = ERODE_BRIEF_NAME if side == "erode" else BRIEF_NAME
    txt = read_material(name, max_chars + 200).strip()
    if not txt and side == "erode":
        return ""
    if not txt:
        return ""
    if len(txt) > max_chars:
        txt = txt[:max_chars] + "\n…（摘要过长已截断，细节可在资料库查阅）"
    return txt


def materials_block(our_side: str = "", max_chars: int = MAX_BRIEF_CHARS,
                    allow: bool = True) -> str:
    """拼成可直接追加到提示词尾部的段落；无资料时返回空串。

    allow=False 时一律不注入（反方训练版用：资料只给正方训练版与对话模式）。
    """
    if not allow:
        return ""
    txt = brief(our_side, max_chars)
    if not txt:
        return ""
    return (
        "【本队已定共识与打法（内部资料）】\n"
        "以下是你所在队伍赛前讨论形成的立场、打法与红线。发言时必须与之一致，"
        "但要用自然口语表达，不要背诵术语、不要罗列条目、不要说“根据我们的资料”。\n\n"
        + txt
    )
