import sys, json, time
t0 = time.time()
for line in sys.stdin:
    line = line.strip()
    if not line.startswith("data:"):
        continue
    try:
        ev = json.loads(line[5:].strip())
    except Exception:
        continue
    t = ev.get("type")
    if t in ("sentence", "warn", "flag", "error"):
        print("[%6.0fms] %s: %s" % ((time.time() - t0) * 1000, t,
                                    ev.get("text") or ev.get("error")))
    elif t == "stage" and ev.get("status") == "done":
        print("[%6.0fms] stage: %s" % ((time.time() - t0) * 1000, ev.get("stage")))
    elif t == "done":
        m = ev.get("meta", {})
        u = m.get("usage") or {}
        print("DONE winner=%s first_ms=%s elapsed=%ss chars=%s tokens=%s calls=%s flags=%d" % (
            m.get("race_winner"), m.get("first_sentence_ms"), ev.get("elapsed"),
            m.get("chars"), u.get("total_tokens"), u.get("calls"), len(ev.get("flags") or [])))
