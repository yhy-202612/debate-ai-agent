"""M3 验收指标：首句延迟、事实核查拦截率、可采纳率、打断响应时延。

进程内聚合 + best-effort 落盘（DEBATE_DATA_DIR/metrics.jsonl，未配置则仅内存）。
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque

from .config import data_dir

_LOCK = threading.Lock()
_RUNS: deque = deque(maxlen=1000)
_FEED: deque = deque(maxlen=1000)


def _persist(kind: str, payload: dict) -> None:
    directory = data_dir()
    if not directory:
        return
    try:
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "metrics.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": kind, "ts": int(time.time()), **payload},
                                ensure_ascii=False) + "\n")
    except OSError:
        pass


def record_run(*, mode: str, first_ms, sentences: int, flags: int, usage: dict | None) -> None:
    item = {"mode": mode, "first_ms": first_ms, "sentences": sentences,
            "flags": flags, "usage": usage or {}}
    with _LOCK:
        _RUNS.append(item)
    _persist("run", item)


def record_feedback(*, score, interrupt_ms, note, mode) -> None:
    item = {"score": score, "interrupt_ms": interrupt_ms,
            "note": (note or "")[:200], "mode": mode}
    with _LOCK:
        _FEED.append(item)
    _persist("feedback", item)


def summary() -> dict:
    with _LOCK:
        runs = list(_RUNS)
        feed = list(_FEED)
    firsts = [r["first_ms"] for r in runs if r.get("first_ms")]
    total_sent = sum(r.get("sentences") or 0 for r in runs)
    total_flags = sum(r.get("flags") or 0 for r in runs)
    scores = [f["score"] for f in feed if isinstance(f.get("score"), (int, float))]
    interrupts = [f["interrupt_ms"] for f in feed
                  if isinstance(f.get("interrupt_ms"), (int, float))]
    tokens = sum((r.get("usage") or {}).get("total_tokens") or 0 for r in runs)
    return {
        "runs": len(runs),
        "avg_first_ms": round(sum(firsts) / len(firsts)) if firsts else None,
        "min_first_ms": min(firsts) if firsts else None,
        "sentences": total_sent,
        "flags": total_flags,
        "guard_rate": round(total_flags / total_sent, 3) if total_sent else None,
        "feedback": len(feed),
        "avg_score": round(sum(scores) / len(scores), 2) if scores else None,
        "avg_interrupt_ms": round(sum(interrupts) / len(interrupts)) if interrupts else None,
        "total_tokens": tokens,
    }
