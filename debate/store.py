"""全自动模式 · 转写文本线上持久化（按会话 JSONL）。

落盘目录 = DEBATE_DATA_DIR/transcripts（默认 /tmp/debate-data）；生产环境建议
挂载持久卷（见 docker-compose.yml）。文本很小，长期留存成本可忽略。
"""
from __future__ import annotations

import json
import os
import re
import time

from .config import data_dir

_ALLOWED = re.compile(r"[^A-Za-z0-9_-]")


def _root() -> str:
    base = (data_dir() or "/tmp/debate-data").strip() or "/tmp/debate-data"
    path = os.path.join(base, "transcripts")
    os.makedirs(path, exist_ok=True)
    return path


def _safe(session: str) -> str:
    return _ALLOWED.sub("", session or "")[:64] or "default"


def _path(session: str) -> str:
    return os.path.join(_root(), _safe(session) + ".jsonl")


def append(session: str, items: list[dict]) -> int:
    """追加若干段；返回实际写入条数。"""
    path = _path(session)
    now = int(time.time() * 1000)
    n = 0
    with open(path, "a", encoding="utf-8") as fh:
        for it in items or []:
            text = str(it.get("text") or "").strip()
            if not text:
                continue
            rec = {"ts": int(it.get("ts") or now),
                   "who": str(it.get("who") or "hall"),
                   "text": text[:2000]}
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    return n


def load(session: str, limit: int = 500) -> list[dict]:
    path = _path(session)
    if not os.path.exists(path):
        return []
    out: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out[-max(1, min(2000, limit)):]


def sessions() -> list[dict]:
    rows: list[dict] = []
    try:
        names = os.listdir(_root())
    except OSError:
        return rows
    for name in names:
        if not name.endswith(".jsonl"):
            continue
        path = os.path.join(_root(), name)
        try:
            size = os.path.getsize(path)
            with open(path, encoding="utf-8") as fh:
                lines = sum(1 for _ in fh)
        except OSError:
            continue
        rows.append({"session": name[:-6], "lines": lines, "bytes": size})
    return sorted(rows, key=lambda r: r["session"])
