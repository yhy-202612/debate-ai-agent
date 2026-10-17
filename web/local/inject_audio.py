#!/usr/bin/env python3
"""把辩论赛全过程测试语音以 data URI 内嵌进 debate-local.html。

用法：python3 inject_audio.py [音频路径] [目标html]
默认：/tmp/debate_48k.mp3  →  ./debate-local.html

幂等：重复运行会先抹掉上一次注入的 data URI，再写入新的。
"""
import base64
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MP3 = sys.argv[1] if len(sys.argv) > 1 else "/tmp/debate_48k.mp3"
HTML = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "debate-local.html")

with open(MP3, "rb") as f:
    data = f.read()
uri = "data:audio/mpeg;base64," + base64.b64encode(data).decode("ascii")

with open(HTML, encoding="utf-8") as f:
    html = f.read()

# 幂等：先清理旧注入
html = re.sub(r'const SIM_AUDIO_SRC = "[^"]*";', 'const SIM_AUDIO_SRC = "__SIM_PLACEHOLDER__";', html)
assert "__SIM_PLACEHOLDER__" in html, "未找到 SIM_AUDIO_SRC 占位符"
html = html.replace('const SIM_AUDIO_SRC = "__SIM_PLACEHOLDER__";',
                    f'const SIM_AUDIO_SRC = "{uri}";')

with open(HTML, "w", encoding="utf-8") as f:
    f.write(html)

print(f"注入完成：{MP3} ({len(data)//1024}KB) → {HTML}")
print(f"  文件大小 {os.path.getsize(HTML)//1024}KB，base64 {len(uri)//1024}KB，时长见音频")
