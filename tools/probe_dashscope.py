"""阿里云百炼（DashScope）TTS 全模型探测 —— 与现有 CosyVoice2 做客观对比。

用法：
    export DASHSCOPE_API_KEY=sk-xxx
    python3 tools/probe_dashscope.py                 # 只测合成（延迟/字节）
    python3 tools/probe_dashscope.py --asr-key sk-xx # 额外做 ASR 回读算字错率

评测口径与现有链路一致：把合成音频送回 ASR 回读，与原文算字错率（CER），越低越清晰。
永不打印 API Key。
"""
from __future__ import annotations

import os
import re
import sys
import time

TEXT = ("各位评委，对方辩友把任务被替代直接等同于人被替代，这一步推理跳跃了："
        "被替代的只是具体任务，而人可以通过学习转向更高价值的工作，这正是效率提升带来的空间。")

# 百炼当前主要 TTS 模型（音色与模型版本绑定，见官方音色列表）
CASES = [
    ("qwen-audio-3.0-tts-plus", "Cherry"),
    ("qwen-audio-3.0-tts-flash", "Cherry"),
    ("cosyvoice-v3.5-plus", "longanyang"),
    ("cosyvoice-v3.5-flash", "longanyang"),
    ("cosyvoice-v3-flash", "longanyang"),
    ("cosyvoice-v2", "longxiaochun_v2"),
    ("MiniMax/speech-2.8-hd", "male-qn-qingse"),
]

ASR_URL = "https://api.siliconflow.cn/v1/audio/transcriptions"
ASR_MODEL = "Qwen/Qwen3-ASR-1.7B"
SAVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_probe")


def norm(s: str) -> str:
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", s or "")


def cer(ref: str, hyp: str) -> float:
    a, b = norm(ref), norm(hyp)
    d = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        nd = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            nd[j] = min(d[j] + 1, nd[j - 1] + 1, d[j - 1] + (ca != cb))
        d = nd
    return d[len(b)] / max(1, len(a))


def synth(model: str, voice: str, seconds: int = 60):
    """流式合成，返回 (首字节秒, 总秒, 音频字节)。"""
    import dashscope
    from dashscope.audio.tts_v2 import SpeechSynthesizer, ResultCallback, AudioFormat

    dashscope.api_key = os.environ["DASHSCOPE_API_KEY"]

    class C(ResultCallback):
        def __init__(self):
            self.buf = bytearray(); self.first = None; self.err = None; self.t0 = time.time()
        def on_data(self, d): 
            if self.first is None:
                self.first = time.time() - self.t0
            self.buf.extend(d)
        def on_error(self, m): self.err = m

    c = C()
    s = SpeechSynthesizer(model=model, voice=voice,
                          format=AudioFormat.WAV_24000HZ_MONO_16BIT, callback=c)
    s.streaming_call(TEXT)
    s.streaming_complete(complete_timeout_millis=seconds * 1000)
    if c.err:
        raise RuntimeError(str(c.err)[:200])
    return c.first, time.time() - c.t0, bytes(c.buf)


def main():
    if not os.environ.get("DASHSCOPE_API_KEY"):
        print("✗ 未设置 DASHSCOPE_API_KEY")
        sys.exit(1)
    asr_key = os.environ.get("SILICONFLOW_API_KEY")
    if "--asr-key" in sys.argv:
        asr_key = sys.argv[sys.argv.index("--asr-key") + 1]
    os.makedirs(SAVE_DIR, exist_ok=True)

    import httpx
    print(f"文本 {len(TEXT)} 字，候选 {len(CASES)} 个\n")
    print(f"{'模型':<26}{'音色':<20}{'首字节':>9}{'总耗时':>9}{'秒数':>7}{'CER':>8}")
    for model, voice in CASES:
        try:
            first, total, audio = synth(model, voice)
        except Exception as e:  # noqa: BLE001
            print(f"{model:<26}{voice:<20}  ✗ {type(e).__name__}: {str(e)[:60]}")
            continue
        dur = max(0.1, (len(audio) - 44) / 2 / 24000)
        path = os.path.join(SAVE_DIR, model.replace("/", "_") + ".wav")
        with open(path, "wb") as f:
            f.write(audio)
        c = "n/a"
        if asr_key and audio:
            try:
                r = httpx.post(ASR_URL, headers={"Authorization": "Bearer " + asr_key},
                               files={"file": ("a.wav", audio, "audio/wav")},
                               data={"model": ASR_MODEL}, timeout=180)
                hyp = (r.json() or {}).get("text", "")
                c = f"{cer(TEXT, hyp) * 100:.1f}%"
            except Exception:  # noqa: BLE001
                c = "err"
        print(f"{model:<26}{voice:<20}{first:>8.2f}s{total:>8.2f}s{dur:>6.1f}s{c:>8}")


if __name__ == "__main__":
    main()
