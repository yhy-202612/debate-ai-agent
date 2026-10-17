"""M4：全自动模式模块单测（转写持久化 store + 现场指挥 conductor）。

无语义依赖，纯本地桩，可直接 `PYTHONPATH=. python3 tools/test_m4.py` 运行。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

os.environ["DEBATE_DATA_DIR"] = tempfile.mkdtemp(prefix="debate-data-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from debate import conductor, store  # noqa: E402


class Stub:
    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system, user, *, temperature=0.6, max_tokens=None, on_usage=None):
        self.calls.append((system, user))
        if on_usage:
            on_usage({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        return self.reply


def test_store():
    n = store.append("sess1", [{"who": "opp", "text": "对方发言一"},
                               {"who": "ours", "text": "我方回应"},
                               {"who": "x", "text": "   "}])   # 空文本不入库
    assert n == 2, n
    store.append("sess1", [{"who": "hall", "text": "主持人说"}])
    rows = store.load("sess1")
    assert len(rows) == 3, rows
    assert rows[0]["who"] == "opp" and rows[2]["who"] == "hall"
    assert rows[0]["text"] == "对方发言一"
    # 会话名安全（路径穿越被清洗）
    store.append("../../etc/passwd", [{"who": "opp", "text": "越权"}])
    names = {s["session"] for s in store.sessions()}
    assert "sess1" in names
    assert all(".." not in s and "/" not in s for s in names), names
    # limit 生效
    assert len(store.load("sess1", limit=2)) == 2
    print("STORE_OK", sorted(names))


def test_attr():
    stub = Stub('[{"idx":1,"who":"opp"},{"idx":2,"who":"ours"}]')
    items = asyncio.run(conductor.attribute(stub, ["对方说", "我方说", "会场说"], our_side="正方"))
    assert [i["who"] for i in items] == ["opp", "ours", "hall"], items   # 未判定回退 hall
    assert items[0]["text"] == "对方说"
    print("ATTR_OK")


def test_attr_robust():
    # 代码块包裹 + 大小写归一
    stub = Stub("```json\n[{\"idx\":1,\"who\":\"OPP\"}]\n```")
    assert asyncio.run(conductor.attribute(stub, ["x"]))[0]["who"] == "opp"
    # 非法输出整体回退 hall
    stub2 = Stub("无法判断")
    assert asyncio.run(conductor.attribute(stub2, ["y"]))[0]["who"] == "hall"
    # 空输入
    assert asyncio.run(conductor.attribute(stub2, [])) == []
    print("ATTR_ROBUST_OK")


def test_speak():
    r = asyncio.run(conductor.should_speak(Stub('{"speak":true,"reason":"对方已说完"}'),
                                           ["[对方] 我方认为……"]))
    assert r["speak"] is True and "对方" in r["reason"], r
    r2 = asyncio.run(conductor.should_speak(Stub('{"speak":false,"reason":"我方刚说完"}'),
                                            ["[我方] 刚才说"]))
    assert r2["speak"] is False, r2
    # 空输入 / 非法输出 → 安全 false
    assert asyncio.run(conductor.should_speak(Stub("x"), []))["speak"] is False
    assert asyncio.run(conductor.should_speak(Stub("not json"), ["[对方] x"]))["speak"] is False
    print("SPEAK_OK")


if __name__ == "__main__":
    test_store()
    test_attr()
    test_attr_robust()
    test_speak()
    print("M4_CONDUCTOR_TESTS_OK")
