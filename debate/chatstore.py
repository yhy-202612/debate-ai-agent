"""对话数据云端存储：落盘到 DEBATE_DATA_DIR/chat，容器重建不丢。

一个会话一个 JSON 文件，结构：
{
  "sid": "xxx",
  "updatedAt": 1700000000,
  "messages": [{"role":"user|assistant","content":"...","ts":1700000000000}],
  "memory":   {"notes":[],"prefs":[],"topics":[],"summary":"","lastSummedIndex":0},
  "distill":  {"text":"...","name":"...","updatedAt":0}
}
"""
from __future__ import annotations

import json
import os
import re
import time

from .config import data_dir

_SID_RE = re.compile(r"^[A-Za-z0-9_-]{4,48}$")
_MAX_MESSAGES = 800
_MAX_DISTILL = 200_000


def _dir() -> str:
    p = os.path.join(data_dir(), "chat")
    os.makedirs(p, exist_ok=True)
    return p


def _path(sid: str) -> str:
    sid = str(sid or "").strip()
    if not _SID_RE.match(sid):
        raise ValueError("会话 id 非法（4~48 位字母数字/下划线/连字符）")
    return os.path.join(_dir(), sid + ".json")


def _blank(sid: str) -> dict:
    return {
        "sid": sid,
        "updatedAt": int(time.time()),
        "messages": [],
        "memory": {"notes": [], "prefs": [], "topics": [], "summary": "", "lastSummedIndex": 0},
        "distill": {"text": "", "name": "", "updatedAt": 0},
    }


def load(sid: str) -> dict:
    p = _path(sid)
    if not os.path.exists(p):
        return _blank(sid)
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return _blank(sid)
    base = _blank(sid)
    if isinstance(data, dict):
        base.update({k: v for k, v in data.items() if k in base})
    if not isinstance(base.get("memory"), dict):
        base["memory"] = _blank(sid)["memory"]
    if not isinstance(base.get("distill"), dict):
        base["distill"] = _blank(sid)["distill"]
    return base


def save(sid: str, patch: dict) -> dict:
    """部分更新：只覆盖显式传入的字段。"""
    cur = load(sid)
    if isinstance(patch.get("messages"), list):
        msgs = []
        for m in patch["messages"][-_MAX_MESSAGES:]:
            if not isinstance(m, dict):
                continue
            c = str(m.get("content") or "").strip()
            if not c:
                continue
            msgs.append({"role": "assistant" if m.get("role") in ("assistant", "ai") else "user",
                         "content": c[:4000],
                         "ts": int(m.get("ts") or 0)})
        cur["messages"] = msgs
    if isinstance(patch.get("memory"), dict):
        mem = cur.get("memory") or {}
        for k in ("notes", "prefs", "topics"):
            if isinstance(patch["memory"].get(k), list):
                mem[k] = patch["memory"][k]
        if "summary" in patch["memory"]:
            mem["summary"] = str(patch["memory"]["summary"] or "")[:2000]
        if "lastSummedIndex" in patch["memory"]:
            try:
                mem["lastSummedIndex"] = max(0, int(patch["memory"]["lastSummedIndex"]))
            except Exception:
                pass
        cur["memory"] = mem
    if isinstance(patch.get("distill"), dict):
        cur["distill"] = {
            "text": str(patch["distill"].get("text") or "")[:_MAX_DISTILL],
            "name": str(patch["distill"].get("name") or "")[:120],
            "updatedAt": int(time.time()),
        }
    cur["updatedAt"] = int(time.time())
    p = _path(sid)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False)
    os.replace(tmp, p)
    return cur


def delete(sid: str) -> bool:
    p = _path(sid)
    if os.path.exists(p):
        os.remove(p)
        return True
    return False


def list_sessions(limit: int = 50) -> list[dict]:
    out: list[dict] = []
    d = _dir()
    for name in os.listdir(d):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, name), "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        mem = data.get("memory") or {}
        out.append({
            "sid": data.get("sid") or name[:-5],
            "updatedAt": data.get("updatedAt") or 0,
            "messages": len(data.get("messages") or []),
            "notes": len(mem.get("notes") or []),
            "prefs": len(mem.get("prefs") or []),
            "summary": str(mem.get("summary") or "")[:80],
        })
    out.sort(key=lambda x: x["updatedAt"], reverse=True)
    return out[:limit]
