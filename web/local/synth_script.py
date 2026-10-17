#!/usr/bin/env python3
"""按 debate-script.json 合成辩论赛全过程测试语音（多角色 + 主席 + 段间停顿）。

用法：python3 synth_script.py [脚本json] [输出wav]
默认：./debate-script.json → /tmp/debate_full.wav
合成后另用 ffmpeg 转 16kHz 单声道 MP3（48kbps）再注入 HTML。
"""
import asyncio
import io
import json
import os
import sys
import wave

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from debate import tts  # noqa: E402

SR = 24000
MODEL = "qwen-audio-3.1-tts-flash"
RATE = "+8%"
CONCURRENCY = 3

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "debate-script.json")
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/debate_full.wav"

with open(SCRIPT, encoding="utf-8") as f:
    doc = json.load(f)

roles = doc["roles"]
segs = doc["segments"]
print(f"脚本：{len(segs)} 段，角色 {len(roles)} 个，模型 {MODEL}")

sem = asyncio.Semaphore(CONCURRENCY)


async def one(i, seg):
    async with sem:
        voice = roles[seg["role"]]["voice"]
        for attempt in range(3):
            try:
                wav = await tts.synthesize(seg["text"], voice=voice, rate=RATE, model=MODEL)
                print(f"  [{i+1:02d}/{len(segs)}] {seg['stage']:<22} {seg['role']:<6} "
                      f"{len(wav)//1024:>4}KB ok")
                return i, wav
            except Exception as e:  # noqa: BLE001
                print(f"  [{i+1:02d}] {seg['stage']} 第{attempt+1}次失败：{str(e)[:120]}")
                await asyncio.sleep(1.5)
        raise RuntimeError(f"第 {i+1} 段合成失败：{seg['stage']}")


async def main():
    results = await asyncio.gather(*[one(i, s) for i, s in enumerate(segs)])
    results.sort(key=lambda x: x[0])
    frames = []
    for i, wav in results:
        with wave.open(io.BytesIO(wav), "rb") as w:
            assert w.getframerate() == SR, f"采样率异常 {w.getframerate()}"
            assert w.getnchannels() == 1, "非单声道"
            assert w.getsampwidth() == 2, "非 16bit"
            frames.append(w.readframes(w.getnframes()))
        pause = float(segs[i].get("pause_after") or 0)
        if pause > 0:
            frames.append(b"\x00\x00" * int(SR * pause))
    data = b"".join(frames)
    with wave.open(OUT, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data)
    dur = len(data) / 2 / SR
    print(f"\n完成 → {OUT}：{len(data)//1024//1024}MB，总时长 {dur:.1f} 秒 = {dur/60:.2f} 分钟")


asyncio.run(main())
