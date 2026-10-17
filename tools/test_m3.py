"""M3 引擎单测：spec 投机竞速、token 用量核算、指标记录。不发起真实网络请求。

运行： PYTHONPATH=. python3 tools/test_m3.py
"""
from __future__ import annotations

import asyncio

from debate import engine as eng
from debate import metrics
from debate.config import Route
from debate.models import ChatClient

SPEECH = ('===SPEECH===\n'
          '{"sentences":["对方同学说人工智能会让人失业。","但这忽略了新岗位会被创造出来。"]}\n'
          '===ANALYSIS===\n'
          '{"propositions":[{"id":"P1","claim":"AI导致失业","evidence":[]}],'
          '"plan":[{"target":"P1","angle":"因果跳跃","line":"失业是新旧更替"}]}')

CALLS = {"fast": 0, "strong": 0}


def _route(alias, model):
    return Route(alias=alias, base_url="http://x/v1", api_key="k", model=model)


async def fake_stream(self, system, user, *, temperature=0.6, max_tokens=None,
                      on_reasoning=None, on_usage=None):
    strong = self.route.alias == "strong"
    CALLS["strong" if strong else "fast"] += 1
    if strong:
        await asyncio.sleep(0.08)   # 强路故意慢，验证快速路抢跑
    if on_usage:
        on_usage({"prompt_tokens": 100, "completion_tokens": 50})
    for i in range(0, len(SPEECH), 9):
        yield SPEECH[i:i + 9]
        if strong:                  # 快速路零让出 → 确定性先出首句
            await asyncio.sleep(0)


async def fake_complete(self, system, user, *, temperature=0.6, max_tokens=None, on_usage=None):
    if on_usage:
        on_usage({"prompt_tokens": 80, "completion_tokens": 40})
    # 同时满足 build_analysis（propositions/plan）与 parse_sentence_list（sentences）
    return ('{"propositions":[{"id":"P1","claim":"AI导致失业","evidence":[]}],'
            '"plan":[{"target":"P1","angle":"因果跳跃","line":"失业是新旧更替"}],'
            '"sentences":["并行候选句一。","并行候选句二。"]}')


ChatClient.stream = fake_stream
ChatClient.complete = fake_complete


async def run_mode(mode_value):
    e = eng.Engine(routes={"fast": _route("fast", "deepseek-flash"),
                           "strong": _route("strong", "deepseek-pro"),
                           "backup": None})
    e.mode = mode_value
    events = []
    async for ev in e.rebut_stream(opponent="AI会导致失业", our_side="积极",
                                   our_case="AI创造新岗位", topic="AI", duration=45):
        events.append(ev)
    return events


def done_of(events):
    got = [e for e in events if e.get("type") == "done"]
    assert got, "未产出 done 事件"
    return got[0]


def main():
    import os
    os.environ["DEBATE_FACTCHECK"] = "off"   # 主体测试先关核查，保持用量断言稳定
    # ---- spec 投机竞速：快速路应先出首句并胜出 ----
    d = done_of(asyncio.run(run_mode("spec")))
    assert d["meta"]["mode"] == "spec", d["meta"]
    assert d["meta"]["race_winner"] == "fast", ("快速路应胜出", d["meta"])
    assert len(d["sentences"]) == 2, d["sentences"]
    u = d["meta"]["usage"]
    assert u["total_tokens"] == 300 and u["calls"] == 2, u   # 两路各 150

    # ---- one_shot：单路 ----
    d2 = done_of(asyncio.run(run_mode("one_shot")))
    assert d2["meta"]["mode"] == "one_shot"
    assert d2["meta"]["usage"]["total_tokens"] == 150, d2["meta"]["usage"]

    # ---- two_stage：结构+并行（含 aux） ----
    d3 = done_of(asyncio.run(run_mode("two_stage")))
    assert d3["meta"]["mode"] == "two_stage"
    assert d3["meta"]["usage"]["total_tokens"] > 150, d3["meta"]["usage"]

    # ---- 指标记录 ----
    s = metrics.summary()
    assert s["runs"] == 3, s
    assert s["avg_first_ms"] is not None, s
    assert s["total_tokens"] == 300 + 150 + d3["meta"]["usage"]["total_tokens"], s
    metrics.record_feedback(score=4, interrupt_ms=120, note="ok", mode="interval")
    s2 = metrics.summary()
    assert s2["feedback"] == 1 and s2["avg_score"] == 4.0 and s2["avg_interrupt_ms"] == 120, s2
    # ---- 事实核查：预筛 + 解析 + 分级 ----
    from debate import factcheck
    parsed = factcheck._parse_list('```json\n[{"idx":2,"level":"severe","problem":"无出处的百分比","suggest":"改为道理论证"}]\n```')
    assert parsed and parsed[0]["idx"] == 2 and parsed[0]["level"] == "severe", parsed
    assert factcheck._parse_list("这不是 JSON") == []
    cand = factcheck._candidates(["人工智能会让百分之九十的人失业。", "这是纯粹的讲道理。", "所有技术都必然带来失业。"])
    assert [i for i, _ in cand] == [1, 3], cand
    assert factcheck._candidates(["这是纯粹的讲道理。"]) == []
    os.environ["DEBATE_FACTCHECK"] = "on"
    d4 = done_of(asyncio.run(run_mode("spec")))
    fc = d4["meta"].get("factcheck")
    assert fc and fc["sentences"] >= 1, fc
    assert d4["meta"]["usage"]["calls"] >= 2, d4["meta"]["usage"]
    print("FACTCHECK_OK", fc)

    # ---- 流式句子提取：句内英文引号不应截断句尾 ----
    seg = '{"sentences":["他说所谓"进步"其实是代价转移。","第二句完整。"]}'
    got = eng.extract_sentences(seg)
    assert got and got[0] == '他说所谓"进步"其实是代价转移。', got
    assert got[1] == "第二句完整。", got
    assert eng.extract_sentences('{"sentences":["hello world"]}') == []   # 纯非中文不输出
    print("EXTRACT_QUOTE_OK")

    # ---- 模型切换：所选模型应回传到 done.meta.model ----
    e2 = eng.Engine(routes={"fast": _route("fast", "deepseek-flash"),
                            "strong": _route("strong", "glm-5.3"), "backup": None})
    e2.mode = "one_shot"
    evs = []

    async def _go():
        async for ev in e2.rebut_stream(opponent="x", our_side="积极", our_case="c",
                                        topic="t", duration=45, model="glm-5.3"):
            evs.append(ev)

    asyncio.run(_go())
    dm = done_of(evs)
    assert dm["meta"]["model"] == "glm-5.3", dm["meta"]
    print("MODEL_SWITCH_OK")

    # 知识库（权威语料提纯）与说话人分离解析
    from debate import knowledge as kb
    block = kb.knowledge_block()
    assert "图尔敏" in block and "立论四要素" in block, "知识库缺少论证框架"
    assert "总结陈词" in block and "质询应对" in block, "知识库缺少总结/质询"
    assert len(kb.FALLACIES) >= 20, ("谬误条目应≥20", len(kb.FALLACIES))
    assert len(kb.TOPIC_HINTS) >= 6, ("辩题方向应≥6", len(kb.TOPIC_HINTS))
    print("KNOWLEDGE_OK", {"fallacies": len(kb.FALLACIES), "topics": len(kb.TOPIC_HINTS),
                           "chars": len(block)})

    from debate import asr as asr_mod
    segs = asr_mod.parse_diarize("1: 甲说了主张\n2: 乙反驳\n1: 甲补充")
    assert [s["speaker"] for s in segs] == ["1", "2", "1"], segs
    assert asr_mod.parse_diarize("没有编号的普通文本") == []
    assert "弊大于利" in asr_mod.FIX_PROMPT and "驳论" in asr_mod.FIX_PROMPT, "纠错提示未含辩论术语"
    print("DIARIZE_OK", segs)

    print("M3_ENGINE_TESTS_OK")
    print("  summary:", {k: s2[k] for k in ("runs", "avg_first_ms", "avg_score",
                                           "avg_interrupt_ms", "guard_rate", "total_tokens")})


main()
