import glob
import os
import wave

rows = []
for f in sorted(glob.glob("/var/lib/debate-agent/tts_exp/*.wav")):
    try:
        w = wave.open(f)
        n, sr, sw, ch = w.getnframes(), w.getframerate(), w.getsampwidth(), w.getnchannels()
        w.close()
        rows.append((os.path.basename(f), n / sr, sr, sw * 8, ch, os.path.getsize(f)))
    except Exception as exc:  # noqa: BLE001
        rows.append((os.path.basename(f), -1, 0, 0, 0, os.path.getsize(f)))

for name, dur, sr, bits, ch, size in rows:
    print(f"{name:24s} {dur:6.2f}s  {sr}Hz {bits}bit ch{ch}  {size}B")

print()
for tag in ("cosy", "qa31"):
    base = next((r for r in rows if r[0] == f"{tag}_A_base.wav"), None)
    if not base or base[1] <= 0:
        continue
    print(f"-- {tag} 基线 {base[1]:.2f}s --")
    for r in rows:
        if r[0].startswith(tag + "_") and r[0] != base[0] and r[1] > 0:
            print(f"   {r[0][len(tag)+1:]:22s} {r[1]:6.2f}s  x{r[1]/base[1]:.2f}")
