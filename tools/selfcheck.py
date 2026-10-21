# -*- coding: utf-8 -*-
"""辩论智能体 · 全链路自检（在容器内跑，真实调用各端点）"""
import json, time, urllib.request, urllib.error, sys

B = "http://127.0.0.1:8000"
R = []
def rec(name, ok, detail=""):
    R.append((name, ok, detail)); print(("  ✓ " if ok else "  ✗ ") + name + (("  " + str(detail)[:150]) if detail else ""))

def post(path, body, timeout=240):
    req = urllib.request.Request(B + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)
def post_raw(path, raw, ct, timeout=180):
    req = urllib.request.Request(B + path, data=raw, headers={"Content-Type": ct})
    return urllib.request.urlopen(req, timeout=timeout)
def get(path, timeout=60):
    return urllib.request.urlopen(B + path, timeout=timeout)
def jget(path, timeout=60):
    return json.loads(get(path, timeout).read().decode("utf-8", "ignore"))
def sse(path, body, timeout=240):
    r = post(path, body, timeout=timeout); out = []
    while True:
        ln = r.readline()
        if not ln: break
        s = ln.decode("utf-8", "ignore").strip()
        if not s.startswith("data:"): continue
        try: out.append(json.loads(s[5:].strip()))
        except Exception: pass
    return out
def wav_sec(b):
    i = b.find(b"data")
    if i < 0: return len(b) / 2 / 24000
    d = int.from_bytes(b[i+4:i+8], "little")
    if d <= 0 or i+8+d > len(b): d = len(b) - (i+8)
    return d / 2 / 24000

TOPIC = "人工智能的发展会消解还是拓展人的价值"
CASE = {"拓展": "我方认为，AI 让人从执行者变成定义者，人的价值被拓展。",
        "消解": "我方认为，AI 接管判断与技能后，人的价值锚点被抽走。"}
OPP = "对方说：人工智能取代大量岗位，让人的技能失去价值，所以人的价值正在被消解。"
SIDE = {"拓展": "反方", "消解": "正方"}

# ---------- 0. 健康 ----------
try:
    h = jget("/healthz"); rec("healthz 正常", bool(h.get("ok")), h.get("engine", {}).get("mode"))
except Exception as e:
    rec("healthz 正常", False, e)

# ---------- 1. 模型清单 ----------
try:
    ms = jget("/api/models")
    ready = [m for m in ms.get("models", []) if m.get("ready")]
    rec("出稿模型可用", len(ready) >= 1, f"{len(ready)} 个 ready / 共 {len(ms.get('models', []))}")
except Exception as e:
    rec("出稿模型可用", False, e)

# ---------- 2. 音色与情感 ----------
try:
    v = jget("/api/voices?model=qwen-audio-3.0-tts-plus")
    # 各模型可选音色在 models[].voices（顶层 voices 是服务端默认模型的音色）
    plus = next((m for m in v.get("models", []) if m.get("id") == "qwen-audio-3.0-tts-plus"), {})
    ids = [x[0] if isinstance(x, (list, tuple)) else x.get("id") for x in plus.get("voices", [])]
    emo = v.get("emotions", [])
    rec("TTS 模型清单", v.get("default_model") is not None, v.get("default_model"))
    rec("plus 男声可选", "longanlufeng" in ids, ids)
    rec("plus 女声可选", "longanlingxin" in ids, ids)
    rec("严肃情感可选", "严肃" in emo, emo[:6])
except Exception as e:
    rec("音色清单", False, e)

# ---------- 3. TTS 语速（男/女） ----------
TTS_TEXT = "我方认为，对方把工具和能力混为一谈了。那么请问对方，计算器问世当年，人的计算能力是不是就该消失了？"
AUDIO = {}
for label, voice, rate in [("男声 longanlufeng +8%", "longanlufeng", "+8%"),
                           ("女声 longanlingxin +0%", "longanlingxin", "+0%")]:
    try:
        r = post("/api/tts-stream", {"text": TTS_TEXT, "voice": voice, "rate": rate,
                                     "model": "qwen-audio-3.0-tts-plus", "emotion": "严肃"})
        buf = r.read(); sec = wav_sec(buf); cpm = len(TTS_TEXT) / sec * 60 if sec else 0
        AUDIO[voice] = buf
        rec(f"TTS {label}", sec > 1, f"{sec:.1f}s ≈ {cpm:.0f} 字/分")
    except Exception as e:
        rec(f"TTS {label}", False, e)

# ---------- 4. 级联出稿：两方 ----------
CASCADE = {}
for stance in ("拓展", "消解"):
    try:
        t0 = time.time(); evs = sse("/api/rebut", {
            "topic": TOPIC, "our_side": SIDE[stance], "stance": stance,
            "our_case": CASE[stance], "opponent": OPP, "duration": 15})
        sents = [e.get("text") for e in evs if e.get("type") == "sentence"]
        body = "".join(sents)
        first = next((e.get("meta", {}).get("first_sentence_ms") for e in evs if e.get("type") == "done"), None)
        CASCADE[stance] = body
        rec(f"级联出稿 {SIDE[stance]}·{stance}", len(body) > 40,
            f"{len(body)} 字 / 首句 {first}ms / 总 {time.time()-t0:.1f}s")
    except Exception as e:
        rec(f"级联出稿 {SIDE[stance]}·{stance}", False, e)

# ---------- 5. 端到端出稿：两方 ----------
for stance in ("拓展", "消解"):
    try:
        t0 = time.time(); evs = sse("/api/rebut-omni", {
            "topic": TOPIC, "our_side": SIDE[stance], "stance": stance,
            "our_case": CASE[stance], "opponent": OPP, "duration": 15,
            "voice": "Ethan" if stance == "拓展" else "Serena"})
        txt = "".join(e.get("delta", "") for e in evs if e.get("type") == "text")
        aud = sum(len(e.get("delta", "")) for e in evs if e.get("type") == "audio")
        err = [e for e in evs if e.get("type") == "error"]
        rec(f"端到端出稿 {SIDE[stance]}·{stance}", len(txt) > 30 and not err,
            f"{len(txt)} 字 / 音频块 {aud} / 总 {time.time()-t0:.1f}s" + (f" / ERR {err[:1]}" if err else ""))
    except Exception as e:
        rec(f"端到端出稿 {SIDE[stance]}·{stance}", False, e)

# ---------- 6. 说话人归属（含主席串场） ----------
try:
    a = json.loads(post("/api/attribute", {"texts": ["感谢正方一辩。下面有请反方一辩进行立论陈词。我方认为人工智能会拓展人的价值。"],
                                           "our_side": "反方", "topic": TOPIC}).read().decode())
    rec("归属接口返回", bool(a), str(a)[:120])
except Exception as e:
    rec("归属接口返回", False, e)

# ---------- 7. 判停（该不该说） ----------
try:
    s1 = json.loads(post("/api/should-speak", {"recent": ["[对方] 我方认为人工智能会消解人的价值，这是不容置疑的。"],
                                               "our_side": "反方", "gap_ms": 6000, "tail_complete": True}).read().decode())
    s2 = json.loads(post("/api/should-speak", {"recent": ["[对方] 我方认为人工智能会"],
                                               "our_side": "反方", "gap_ms": 600, "tail_complete": False}).read().decode())
    rec("判停：静默6s+收束 → 该说", s1.get("speak") is True, s1)
    rec("判停：静默0.6s → 不该说", s2.get("speak") is False, s2)
except Exception as e:
    rec("判停接口", False, e)

# ---------- 8. 长期总结 ----------
try:
    sm = json.loads(post("/api/summarize", {"prev": None, "recent": [
        "[我方] 我方用京东客服雨辰的例子说明人从执行者变成规则定义者。",
        "[对方] 对方说 AI 取代岗位会让人失去价值。",
        "[我方] 我方又问相机普及是否消解了画家的价值。"]}).read().decode())
    ev = sm.get("our_evidence") or []
    rec("长期总结产出", bool(sm.get("our_points") or ev), f"我方论据 {len(ev)} 条")
    rec("总结记录我方已用例子", any("雨辰" in str(x) or "相机" in str(x) for x in ev), ev)
except Exception as e:
    rec("长期总结产出", False, e)

# ---------- 9. 按钮通道 ----------
try:
    post("/api/buzzer", {"side": "con", "action": "down", "source": "button", "seat": 1}).read()
    st = jget("/api/buzzer/state")
    rec("按钮发令写入", bool(st), str(st)[:100])
except Exception as e:
    rec("按钮发令写入", False, e)

# ---------- 10. ASR（用刚合成的男声音频回读） ----------
try:
    raw = AUDIO.get("longanlufeng")
    if raw:
        t = post_raw("/api/asr", raw, "audio/wav", timeout=120).read().decode("utf-8", "ignore")
        d = json.loads(t)
        txt = d.get("text") or ""
        hit = sum(1 for k in ("工具", "计算器", "计算能力") if k in txt)
        rec("ASR 回读", len(txt) > 5, f"「{txt[:60]}…」命中关键词 {hit}/3")
    else:
        rec("ASR 回读", False, "无音频输入")
except Exception as e:
    rec("ASR 回读", False, e)

# ---------- 11. 统计 ----------
try:
    st = jget("/api/stats"); rec("统计接口", bool(st), str(st)[:120])
except Exception as e:
    rec("统计接口", False, e)

ok = sum(1 for _, o, _ in R if o); bad = len(R) - ok
print(f"\n===== 自检结果：{ok} 通过 / {bad} 失败 =====")
for n, o, d in R:
    if not o: print("  失败项:", n, d)
