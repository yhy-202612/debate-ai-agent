"""模拟前端「常开语音边录边识别」链路：
把 24k WAV 重采样成 16k（前端 ScriptProcessor 的采样率）、切成多片（模拟 VAD 自动断句），
逐片 POST /debate/api/asr，验证「分片 → 识别 → 拼接」是否正确。
"""
import io
import json
import struct
import urllib.request
import wave

SRC = "/var/lib/debate-agent/tts_exp/qa31_A_base.wav"
API = "http://127.0.0.1:51318/debate/api/asr"


def read_pcm(path):
    raw = open(path, "rb").read()
    # 该 WAV 的 data size 是流式占位值，按实际长度算
    i = raw.find(b"data")
    fmt = raw.find(b"fmt ")
    ch = struct.unpack("<H", raw[fmt + 10:fmt + 12])[0]
    sr = struct.unpack("<I", raw[fmt + 12:fmt + 16])[0]
    bits = struct.unpack("<H", raw[fmt + 22:fmt + 24])[0]
    pcm = raw[i + 8:]
    pcm = pcm[: len(pcm) // 2 * 2]
    return pcm, ch, sr, bits


def resample(pcm, sr_in, sr_out):
    n_in = len(pcm) // 2
    src = struct.unpack("<%dh" % n_in, pcm)
    n_out = int(n_in * sr_out / sr_in)
    out = []
    for i in range(n_out):
        x = i * sr_in / sr_out
        a = int(x)
        b = min(a + 1, n_in - 1)
        f = x - a
        out.append(int(src[a] * (1 - f) + src[b] * f))
    return struct.pack("<%dh" % len(out), *out), len(out)


def to_wav(pcm16, sr):
    buf = io.BytesIO()
    w = wave.open(buf, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(sr)
    w.writeframes(pcm16)
    w.close()
    return buf.getvalue()


def asr(wav_bytes, model=""):
    url = API + ("?model=" + model if model else "")
    req = urllib.request.Request(url, data=wav_bytes,
                                 headers={"Content-Type": "audio/wav"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


pcm, ch, sr, bits = read_pcm(SRC)
print(f"源：{len(pcm)}B {sr}Hz {bits}bit ch{ch}")
pcm16, n = resample(pcm, sr, 16000)
print(f"重采样到 16k：{n} 样本 = {n/16000:.2f}s")

# 切成 3 片（模拟 VAD 按静音自动断句）
cuts = [0, n // 3, n * 2 // 3, n]
texts = []
for k in range(3):
    seg = pcm16[cuts[k] * 2: cuts[k + 1] * 2]
    wb = to_wav(seg, 16000)
    d = asr(wb)
    t = (d.get("text") or "").strip()
    texts.append(t)
    print(f"片{k+1}: {len(seg)//2/16000:.2f}s {len(wb)}B -> {t or d}")

print("\n拼接结果:", "".join(texts))

# 整段一次识别做对照
d = asr(to_wav(pcm16, 16000))
print("整段识别:", (d.get("text") or "").strip())

# 换模型对照
for m in ("dual-qwen3-audio31", "bailian-qwen-audio-3.1-asr-flash", "siliconflow-qwen3-asr-1.7b"):
    d = asr(to_wav(pcm16, 16000), m)
    print(f"[{m}] -> {(d.get('text') or '').strip() or d}")
