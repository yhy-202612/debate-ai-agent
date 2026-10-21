"""TTS「重要词慢速重读」可行性实验：同一句话用不同标记方案合成，比较时长与可听性。

在容器内跑：python3 /app/tools/tts_emphasis_exp.py
产物落 /data/tts_exp/（宿主 /var/lib/debate-agent/tts_exp/）
"""
import asyncio
import os
import sys

sys.path.insert(0, "/app")
from debate import tts  # noqa: E402

OUT = "/data/tts_exp"
os.makedirs(OUT, exist_ok=True)

BASE = "我方认为，这个论点从根本上就是错的。"

CASES = [
    ("A_base", BASE),
    # 破折号 / 省略号：靠标点求停顿（最省事，零成本）
    ("B_dash", "我方认为，这个论点从根本上——就是错的。"),
    ("B2_ellip", "我方认为，这个论点从根本上……就是错的。"),
    # SSML：看模型是执行标签还是把标签当文本念出来
    ("C_ssml", '我方认为，这个论点<break time="500ms"/>从根本上<prosody rate="slow">就是错的</prosody>。'),
    # 情感标签（已知支持）
    ("D_emotion", "[严肃]我方认为，这个论点从根本上就是错的。"),
    # 重复强调：把关键词重复一次
    ("E_repeat", "我方认为，这个论点从根本上就是错的——就是错的。"),
]

MODELS = [
    ("cosy", "FunAudioLLM/CosyVoice2-0.5B"),
    ("qa31", "qwen-audio-3.1-tts-flash"),
]


async def one(tag, name, text, model):
    try:
        b = await asyncio.wait_for(tts.synthesize(text, rate="+8%", model=model), timeout=120)
    except Exception as exc:  # noqa: BLE001
        print(f"ERR {tag} {name} {type(exc).__name__}: {exc}"[:200], flush=True)
        return
    fn = os.path.join(OUT, f"{tag}_{name}.wav")
    with open(fn, "wb") as fh:
        fh.write(b)
    print(f"OK  {tag} {name} {len(b)}B -> {fn}", flush=True)


async def main():
    for tag, model in MODELS:
        for name, text in CASES:
            await one(tag, name, text, model)
        # 慢速重读：同一句用不同语速，验证「局部降速」能否靠分段合成实现
        for r in ("+8%", "-20%", "-35%"):
            await one(tag, "F_rate_" + r.replace("%", "").replace("+", "p").replace("-", "m"),
                      "就是错的", model)


if __name__ == "__main__":
    asyncio.run(main())
    print("ALL DONE", flush=True)
