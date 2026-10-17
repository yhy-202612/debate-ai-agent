"""运行配置与模型路由。

所有密钥仅从环境变量读取，代码中不硬编码。
默认走 DeepSeek 官方；可通过 DEBATE_* 变量切换到 wawapii 中转（GLM / Kimi）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _get(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return default


@dataclass
class Route:
    """一条 OpenAI 兼容的模型路由。"""

    alias: str
    base_url: str
    api_key: str
    model: str
    timeout: float = 90.0
    effort: str = ""  # reasoning_effort：low / high / max（空=不传，用服务端默认）
    no_temperature: bool = False  # Claude 系不支持非默认 temperature → 请求中省略该字段

    @property
    def chat_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    @property
    def ready(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    def describe(self) -> dict:
        return {
            "alias": self.alias,
            "base_url": self.base_url,
            "model": self.model,
            "effort": self.effort,
            "key_set": bool(self.api_key),
        }


def load_routes() -> dict[str, Route]:
    """构建 fast / strong 两条路由（未单独指定时回退到通用配置）。"""
    base = _get("DEBATE_API_URL", "DEEPSEEK_API_BASE", default="https://api.deepseek.com/v1")
    key = _get("DEBATE_API_KEY", "DEEPSEEK_API_KEY")
    model = _get("DEBATE_MODEL", default="deepseek-chat")
    effort = _get("DEBATE_EFFORT")
    try:
        timeout = float(_get("DEBATE_TIMEOUT", default="90"))
    except ValueError:
        timeout = 90.0

    fast = Route(
        alias="fast",
        base_url=_get("DEBATE_FAST_BASE_URL", default=base),
        api_key=_get("DEBATE_FAST_API_KEY", default=key),
        model=_get("DEBATE_FAST_MODEL", default=model),
        timeout=timeout,
        effort=_get("DEBATE_FAST_EFFORT", default=effort),
    )
    strong = Route(
        alias="strong",
        base_url=_get("DEBATE_STRONG_BASE_URL", default=base),
        api_key=_get("DEBATE_STRONG_API_KEY", default=key),
        model=_get("DEBATE_STRONG_MODEL", default=model),
        timeout=timeout,
        effort=_get("DEBATE_STRONG_EFFORT", default=effort),
    )
    backup_route = None
    backup_base = _get("DEBATE_BACKUP_BASE_URL")
    if backup_base:
        backup_route = Route(
            alias="backup",
            base_url=backup_base,
            api_key=_get("DEBATE_BACKUP_API_KEY", default=key),
            model=_get("DEBATE_BACKUP_MODEL", default=model),
            timeout=timeout,
            effort=_get("DEBATE_BACKUP_EFFORT", default=effort),
        )
    return {"fast": fast, "strong": strong, "backup": backup_route}


def mode() -> str:
    """出稿模式：
    - one_shot  单次流式（先出稿、后补结构），首句最快
    - two_stage 结构+策略（快模型）→ 并行多路出稿（强模型），鲁棒性优先
    - spec      投机竞速：快/强两路并行，先出首句者独得全稿（推测解码思路）
    """
    value = _get("DEBATE_MODE", default="one_shot").lower()
    return value if value in ("one_shot", "two_stage", "spec") else "one_shot"


def parallel_width() -> int:
    """M3：并行子个体数量，2–4。"""
    try:
        value = int(_get("DEBATE_PARALLEL", default="3"))
    except ValueError:
        value = 3
    return max(1, min(4, value))


def max_tokens() -> int:
    try:
        return int(_get("DEBATE_MAX_TOKENS", default="4096"))
    except ValueError:
        return 4096


def access_token() -> str:
    """可选的访问口令；为空则不校验（仅依赖网关/内网隔离）。"""
    return _get("DEBATE_ACCESS_TOKEN")


def tts_voice() -> str:
    return _get("DEBATE_TTS_VOICE", default="zh-CN-YunjianNeural")


def tts_rate() -> str:
    return _get("DEBATE_TTS_RATE", default="+8%")


# ---------------- 语音链路（硅基流动 SiliconFlow）----------------

def siliconflow_key() -> str:
    """硅基流动 API Key（用于 ASR 识别与 CosyVoice 合成）。"""
    return _get("SILICONFLOW_API_KEY", "DEBATE_SILICONFLOW_KEY")


def siliconflow_base() -> str:
    return _get("SILICONFLOW_BASE_URL", default="https://api.siliconflow.cn/v1")


# ---------------- 语音链路（阿里云百炼 DashScope，可选）----------------

def dashscope_key() -> str:
    """阿里云百炼 API Key（用于百炼 TTS：CosyVoice / Qwen3-TTS）。"""
    return _get("DASHSCOPE_API_KEY", "DEBATE_DASHSCOPE_KEY")


def dashscope_base_url() -> str:
    return _get("DASHSCOPE_BASE_URL", default="https://dashscope.aliyuncs.com")



def asr_model() -> str:
    """语音识别模型（默认 Qwen3-ASR：实测明显更准，标点规范、少噪声）。"""
    return _get("DEBATE_ASR_MODEL", default="Qwen/Qwen3-ASR-1.7B")


def tts_model() -> str:
    """语音合成模型（CosyVoice 2）。"""
    return _get("DEBATE_TTS_MODEL", default="FunAudioLLM/CosyVoice2-0.5B")


def tts_speaker() -> str:
    """CosyVoice2 音色名（不含模型前缀）。"""
    return _get("DEBATE_TTS_SPEAKER", default="alex")


# ---------------- 可选 ASR 模型（供前端下拉切换）----------------
# provider：siliconflow（硅基流动 OpenAI 兼容 /audio/transcriptions）
#         | bailian（阿里云百炼同步接口 multimodal-generation）
#         | dual（两路并行：主文本用 primary，热词校正借 aux，任一路失败自动降级）

ASR_MODELS: list[dict] = [
    {"id": "dual-qwen3-audio31", "provider": "dual",
     "name": "并行融合 · Qwen3-ASR 正文 + 百炼3.1 热词校正"},
    {"id": "qwen-audio-3.1-asr-flash", "provider": "bailian",
     "name": "百炼 Qwen-Audio-3.1-ASR · 最新（快、可加热词）"},
    {"id": "qwen-audio-3.0-asr-flash", "provider": "bailian",
     "name": "百炼 Qwen-Audio-3.0-ASR · 多语种"},
    {"id": "Qwen/Qwen3-ASR-1.7B", "provider": "siliconflow",
     "name": "Qwen3-ASR-1.7B · 硅基流动（默认）"},
    {"id": "FunAudioLLM/SenseVoiceSmall", "provider": "siliconflow",
     "name": "SenseVoice-Small · 快、通用"},
    {"id": "XingChenAGI/XingChenASR-V3.2-Ultra", "provider": "siliconflow",
     "name": "星辰 ASR · V3.2-Ultra"},
    {"id": "XingChenAGI/XingChenASR-V3.2", "provider": "siliconflow",
     "name": "星辰 ASR · V3.2"},
    {"id": "XingChenAGI/XingChenASR-Diarize-V3.0", "provider": "siliconflow",
     "name": "星辰 ASR · 说话人分离 V3"},
    {"id": "XingChenAGI/XingChenGSR-V1.0", "provider": "siliconflow",
     "name": "星辰 GSR · V1.0"},
]


def asr_provider(model_id: str) -> str:
    """按模型 id 查其所属厂商；未登记则按硅基流动（兼容旧配置）。"""
    for m in ASR_MODELS:
        if m["id"] == model_id:
            return m.get("provider", "siliconflow")
    return "siliconflow"


# 并行融合（dual）的两路模型：正文用 primary，热词校正借 aux
def asr_dual_primary() -> str:
    return _get("DEBATE_ASR_DUAL_PRIMARY", default="Qwen/Qwen3-ASR-1.7B")


def asr_dual_aux() -> str:
    return _get("DEBATE_ASR_DUAL_AUX", default="qwen-audio-3.1-asr-flash")


def asr_dual_aux_timeout() -> float:
    """并行融合时辅路（热词校正）的时间预算（秒）；超时则放弃校正，不拖慢正文。"""
    try:
        return float(_get("DEBATE_ASR_DUAL_AUX_TIMEOUT", default="6") or 6)
    except ValueError:
        return 6.0


# 辩论术语热词表（百炼接口支持即时热词，可显著改善同音误识别）
ASR_HOTWORDS: dict = {
    "立论": 5, "驳论": 5, "陈词": 4, "归谬": 5, "质询": 4, "反驳": 4,
    "弊大于利": 5, "利大于弊": 5, "稻草人": 4, "以偏概全": 4, "滑坡": 4,
    "举证责任": 4, "隐含前提": 4, "衡量标准": 4, "自由辩论": 4,
}


# ---------------- M3 成本核算与指标（token 恒定上报；单价可配）----------------

def _num(name: str):
    value = _get(name)
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def price_in():
    """输入 token 单价（元 / 百万 token）；未配置返回 None。"""
    return _num("DEBATE_PRICE_IN")


def price_out():
    """输出 token 单价（元 / 百万 token）；未配置返回 None。"""
    return _num("DEBATE_PRICE_OUT")


def data_dir() -> str:
    """指标落盘目录；为空则仅内存聚合。"""
    return _get("DEBATE_DATA_DIR")


def _flag(name: str, default: str = "on") -> bool:
    return _get(name, default=default).lower() not in ("off", "0", "false", "no")


def kb_enabled() -> bool:
    """是否注入辩论知识库（谬误库/论证框架）到出稿提示词。"""
    return _flag("DEBATE_KB")


def factcheck_enabled() -> bool:
    """是否在出稿末尾自动做事实核查。"""
    return _flag("DEBATE_FACTCHECK")


# ---------------- 可切换的出稿模型（前端下拉选择）----------------

CHOOSABLE_MODELS: list[dict] = [
    # ── DeepSeek 官方（快、便宜，适合实时判定）──
    {"id": "deepseek-flash", "name": "DeepSeek-V4.1-Flash · 快", "group": "DeepSeek 官方"},
    {"id": "deepseek-chat", "name": "DeepSeek-chat · 通用", "group": "DeepSeek 官方"},
    # ── wawapii 中转 · GLM ──
    {"id": "glm-5.3", "name": "GLM-5.3 · 强推理", "group": "wawapii 中转 · GLM"},
    {"id": "glm-5.3-flash", "name": "GLM-5.3-Flash · 快", "group": "wawapii 中转 · GLM"},
    # ── wawapii 中转 · Claude（实测：quality 高但延迟 3~17s，适合出稿/长期总结）──
    {"id": "claude-opus-5-5", "name": "Claude Opus 5.5 · 最强（慢，会长时间思考）", "group": "wawapii 中转 · Claude"},
    {"id": "claude-sonnet-5-5", "name": "Claude Sonnet 5.5 · 均衡", "group": "wawapii 中转 · Claude"},
    {"id": "claude-fable-5-1", "name": "Claude Fable 5.1 · 均衡", "group": "wawapii 中转 · Claude"},
    {"id": "claude-haiku-5-5", "name": "Claude Haiku 5.5 · 快", "group": "wawapii 中转 · Claude"},
]


def _timeout_default() -> float:
    try:
        return float(_get("DEBATE_TIMEOUT", default="90"))
    except ValueError:
        return 90.0


def _claude_timeout() -> float:
    """Claude 经中转延迟较高（实测单次 3~60s+），给更宽的超时。"""
    try:
        return float(_get("DEBATE_CLAUDE_TIMEOUT", default="180"))
    except ValueError:
        return 180.0


def choice_route(model_id: str) -> Route | None:
    """按可切换模型 id 构造路由；凭据缺省时复用 strong/fast 的配置。

    - glm-* / claude-* / kimi-*  → 走 wawapii 中转（默认复用 DEBATE_STRONG_*）
    - deepseek-*                 → 走 DeepSeek 官方（默认复用 DEBATE_FAST_*）
    可用 DEBATE_GLM_* / DEBATE_CLAUDE_* / DEBATE_DS_* 单独覆盖。

    注意：Claude 系（经中转）不支持非默认 temperature，故 no_temperature=True。
    """
    model_id = (model_id or "").strip()
    if not model_id:
        return None
    if model_id.startswith("claude-"):
        return Route(
            alias=model_id,
            base_url=_get("DEBATE_CLAUDE_BASE_URL", "DEBATE_RELAY_BASE_URL",
                          "DEBATE_GLM_BASE_URL", "DEBATE_STRONG_BASE_URL",
                          default="https://wawapii.com/v1"),
            api_key=_get("DEBATE_CLAUDE_API_KEY", "DEBATE_RELAY_API_KEY",
                         "DEBATE_GLM_API_KEY", "DEBATE_STRONG_API_KEY"),
            model=model_id,
            timeout=_claude_timeout(),
            no_temperature=True,
        )
    if model_id.startswith(("glm-", "kimi-")):
        return Route(
            alias=model_id,
            base_url=_get("DEBATE_GLM_BASE_URL", "DEBATE_RELAY_BASE_URL", "DEBATE_STRONG_BASE_URL",
                          default="https://wawapii.com/v1"),
            api_key=_get("DEBATE_GLM_API_KEY", "DEBATE_RELAY_API_KEY", "DEBATE_STRONG_API_KEY"),
            model=model_id,
            timeout=_timeout_default(),
        )
    if model_id.startswith("deepseek-"):
        return Route(
            alias=model_id,
            base_url=_get("DEBATE_DS_BASE_URL", "DEBATE_FAST_BASE_URL",
                          default="https://api.deepseek.com/v1"),
            api_key=_get("DEBATE_DS_API_KEY", "DEBATE_FAST_API_KEY"),
            model=model_id,
            timeout=_timeout_default(),
            effort=_get("DEBATE_DS_EFFORT", default="low"),
        )
    return None
