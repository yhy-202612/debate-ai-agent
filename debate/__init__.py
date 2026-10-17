"""辩论 AI 二辩 · M1–M3 核心包。

模块划分：
- config   运行配置与模型路由
- models   OpenAI 兼容模型客户端（异步、支持流式）
- prompts  P0–P5 提示词库
- engine   编排出稿引擎（M1 四节点 + M3 提速）
- tts      M2 语音合成（edge-tts 分句流式）
"""

__all__ = ["config", "models", "prompts", "engine", "tts"]
__version__ = "1.0.0"
