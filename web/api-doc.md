# 辩论 AI 二辩 · 接口文档（供其他 AI 工具调用）

> 版本 2026-10-17 · 服务地址 `https://120.48.43.211/debate`
> 本文档面向「用别的 AI 工具（ChatGPT / Claude / 豆包 / 自写脚本）来训练或调用这个辩手」的场景。

---

## 一、基础信息

| 项目 | 值 |
|---|---|
| Base URL | `https://120.48.43.211/debate` |
| HTTP 版本 | 也支持 `http://120.48.43.211/debate`（**页面在 https 下必须用 https，否则被混合内容拦截**） |
| 鉴权 | 当前**无需鉴权**（服务端 `DEBATE_ACCESS_TOKEN` 为空）。若日后开启，请求头带 `Authorization: Bearer <token>` |
| CORS | `*`，任意来源可直接调用 |
| 流式 | `POST /api/rebut`、`/api/rebut-omni` 返回 `text/event-stream` |
| 证书 | 自签证书，脚本里需关闭校验（`curl -k` / Python `verify=False`） |

健康检查：`GET /healthz` → `{"ok":true,...}`

---

## 二、最常用的 4 个端点

### 1) 出稿（流式驳论）`POST /api/rebut`

一次性生成一段 30~60 秒的完整驳论，逐句推送。

**请求体**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `opponent` | string | ✅ | 对方原文（≤12000 字） |
| `our_side` | string | ✅ | `正方` / `反方`（系统会按辩题自动补全为「拓展 / 消解」） |
| `our_case` | string | ✅ | 我方立论要点（≤6000 字） |
| `topic` | string | | 辩题，默认「人工智能的发展会消解还是拓展人的价值」 |
| `duration` | int | | 目标时长秒数，范围 **30–60**，默认 45 |
| `model` | string | | 出稿模型 id，见 `GET /api/models`；留空用默认 |
| `system_extra` | string | | **自定义提示词**（≤3000 字），追加到系统提示词最后，优先级最高。用它把 AI 调成你要的辩风 |
| `no_strategy` | bool | `false` | **纯净模式**。`true` 时不注入辩论技法库与课堂考察口径，只保留语音合成所需的格式约束（纯口语、无 Markdown、无括号旁白、字数按时长折算）。训练版默认开启 |

```bash
curl -k -N -X POST https://120.48.43.211/debate/api/rebut \
  -H 'Content-Type: application/json' \
  -d '{
    "topic":"人工智能的发展会消解还是拓展人的价值",
    "our_side":"正方",
    "our_case":"AI 把重复劳动外包，让人专注创造、判断与关怀。",
    "opponent":"对方说依赖算法会侵蚀判断力与创造力，人的能力会退化。",
    "duration":45
  }'
```

**响应（SSE，每行 `data: {json}`）**

| `type` | 含义 | 关键字段 |
|---|---|---|
| `stage` | 阶段进度 | `stage`: `draft`/`outline`/`guard`/`factcheck`；`status`: `start`/`done` |
| `sentence` | **一句成稿**（整句推送，不是逐字） | `idx`、`text` |
| `flag` | 风险提示 | `text` |
| `warn` | 非致命告警 | `text` |
| `done` | 结束 | `sentences`、`chars`、`ms`、`usage` |

> ⚠️ 注意：本服务是**按整句推送**，首句约 4.5~5 秒（模型要写完整句才发）。要更快请用下面的 Omni 通道。

### 2) 极速应答 `POST /api/quick`

两句话内的接话（人机对辩、自由辩论抢话用）。

```json
// 请求
{"question":"对方说AI会让人变懒，你怎么看？","our_side":"正方","our_case":"…"}
// 响应
{"text":"…","ms":1200}
```

### 3) 端到端出稿（推荐，首音约 1 秒）`POST /api/rebut-omni`

一次生成 → 直接出**字幕 + PCM 音频**，省掉独立 TTS。

请求体同 `/api/rebut`，额外支持 `voice`（见 `GET /api/omni-status`）、`model`。

**响应 SSE**

| `type` | 含义 |
|---|---|
| `text` | 字幕增量（`delta`） |
| `audio` | 音频块（`delta` = base64，**24kHz / 16bit / 单声道 PCM**） |
| `sentence` | 切好的句子（结束时可拿全稿） |
| `done` | 结束（含耗时） |
| `error` | 失败（`message`） |

### 4) 说话人归属判定 `POST /api/attribute`

判断一段转写是谁说的（多人在场时用）。

```json
// 请求
{"texts":["感谢正方一辩，下面有请反方一辩。"],"our_side":"正方","topic":"…","split":true}
// 响应
{"items":[{"idx":0,"who":"hall","parts":[{"who":"hall","text":"…"}]}]}
```
`who` ∈ `opp`（对方）/ `ours`（我方）/ `hall`（主席·会场）

---

## 三、其余端点速查

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/models` | GET | 可选出稿模型清单（含 `ready` 可用性） |
| `/api/voices` | GET | TTS 音色与模型清单（`?model=` 可查某模型支持的音色） |
| `/api/asr-models` | GET | 识别模型清单 |
| `/api/asr` | POST | 上传音频原始字节（`body` 为二进制，`?model=` 指定模型）→ `{"text":"…"}` |
| `/api/tts` | POST | `{text,voice,rate,model,emotion}` → `audio/mpeg` |
| `/api/tts-stream` | POST | 同上，流式 WAV |
| `/api/polish` | POST | `{text}` 润色一句 |
| `/api/factcheck` | POST | `{sentences}` 事实核查 |
| `/api/summarize` | POST | `{prev,recent,model}` → 滚动摘要（长期记忆） |
| `/api/should-speak` | POST | `{recent,our_side,gap_ms,tail_complete}` → 现在是否该我方说 |
| `/api/transcript-save` | POST | `{session,items}` 存转写（≤200 条/次） |
| `/api/transcript-list` | GET | `?session=…&limit=200` 读回 |
| `/api/transcript-sessions` | GET | 列出全部会话 |
| `/api/stats` | GET | 运行指标（出稿次数、首句耗时等） |
| `/api/stage/publish` | POST | 推「AI 正在发言」给大屏：`{type:"speak",side,seat,on,who}` |
| `/api/stage/stream` | GET | 大屏订阅（SSE） |
| `/api/stage/state` | GET | 调试：订阅数 + 最近事件 |
| `/api/asr-realtime` | WS | 实时识别（百炼 realtime，服务端中转） |

---

## 四、Python 调用示例

```python
import json, requests

BASE = "https://120.48.43.211/debate"

def rebut(opponent, our_side="正方", our_case="", duration=45):
    """流式出稿：逐句返回"""
    body = {"topic":"人工智能的发展会消解还是拓展人的价值",
            "our_side":our_side, "our_case":our_case,
            "opponent":opponent, "duration":duration}
    with requests.post(f"{BASE}/api/rebut", json=body, stream=True,
                       verify=False, timeout=180) as r:
        r.raise_for_status()
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            if ev["type"] == "sentence":
                print(ev["idx"], ev["text"])
            elif ev["type"] == "done":
                break

rebut("对方说AI会侵蚀人的判断力，让能力退化。",
      our_case="AI 把重复劳动外包，人回到创造、判断与关怀。")
```

---

## 4.5 自定义提示词 `system_extra`（训练用）

想让 AI 换一种打法，不必改服务端——直接把要求写进 `system_extra`，它会拼到系统提示词**最后**，权重最高，可覆盖默认的技法与风格。

```json
{
  "opponent": "…", "our_side": "反方", "our_case": "…", "duration": 45,
  "system_extra": "每段发言必须先点出对方一个具体的逻辑漏洞，再用一个生活化反例打掉它；结尾必须留一个反问。"
}
```

**实测有效性**：传入「每句话开头必须加『据我看』」这种硬约束，6/6 句全部遵循。

**可直接抄的风格模板**

| 风格 | 提示词要点 |
|---|---|
| 犀利进攻 | 开头一句话点出对方最要命的逻辑漏洞（说清漏在哪）→ 生活化反例打掉 → 抛出必须正面回答的反问；不罗列己方论点，只打对方最弱处 |
| 稳健防守 | 先正面接住对方核心担忧、承认合理部分 → 指出其成立前提已被我方方案消解 → 按「现象→机制→后果」分层推进 |
| 幽默化解 | 用生活类比（计算器、导航、字典、健身器械）把对方推论推到荒谬处；不攻击人，只攻击推论 |
| 举例压人 | 拒绝空泛价值判断，讲清「谁、在什么情况下、具体变好还是变坏」；不确定的数据绝不编造 |

> 服务端上限 3000 字，超长会被截断。该参数同样适用于 `/api/rebut-omni`（端到端通道）。

### 纯净模式 `no_strategy`

服务端默认会额外注入两部分内容：**辩论技法库**（先拆地基、归谬、利弊对冲等 7 条 + 对方常见漏洞清单）与**课堂考察口径**（批判性思维、逻辑思维、提问数、话轮响应、论点层次、进攻总则）。这些是本站比赛版的默认设定。

如果你要训练自己的打法，传 `"no_strategy": true` 把它们全部去掉，只保留：

| 保留项 | 原因 |
|---|---|
| 纯口语、一句一行 | 直接对接语音合成 |
| 不要 Markdown / 标题 / 分点 / 表情 | 合成会念出符号 |
| 不要括号旁白（「（停顿）」） | 会被念出来 |
| 不要「好的」「下面我来说」等过场话 | 浪费时长、显得机器人 |
| 字数按 `duration × 4.5~5.5` | 保证说到目标时长 |
| 不编造数据、不曲解对方主张 | 事实底线 |

**实测对比**（同一辩题、`duration=40`）：

| 模式 | 结果 |
|---|---|
| `no_strategy: true` | 7 句 / 208 字，无设问句，纯按输入内容反驳 |
| `no_strategy: false`（默认） | 4 句，强制含「请问对方…」设问句，按技法主线推进 |

---

## 五、数据分离（重要）

两方训练数据**必须隔离**，规则如下：

| 阵营 | 会话前缀 | 存放位置 |
|---|---|---|
| 正方 | `pro-…` | 服务端 `transcript-save` 的 `session` 字段 |
| 反方 | `con-…` | 同上 |

- 调用方**自己指定 `session`**，只读写自己的前缀；`transcript-sessions` 会列出全部，按前缀自行过滤。
- 前端两版（正方版 / 反方训练版）已分别使用 `debate_local_*` 与 `debate_con_*` 作为 localStorage 命名空间，互不可见。
- 建议外部工具每次训练用一个新 session：`con-<你的工具名>-<时间戳>`。

---

## 六、训练对方 AI 的建议用法

1. **喂对方立论 → 拿我方驳论**：把对面（正方）的立论原文放进 `opponent`，`our_side` 传 `反方`，得到针对性驳论。
2. **循环对练**：把上一轮 AI 的输出当作 `opponent` 再发一次，形成多轮攻防。
3. **只练反应速度**：用 `/api/quick`，每次 1~2 句，适合练接话。
4. **练立论**：把 `our_case` 写细（3 个论点 + 论据），`duration` 调 60，出稿会更完整。
5. **判自己写得好不好**：`/api/factcheck` 查事实，`/api/polish` 润色表达。

> 提示：`our_side` 传「反方」时，系统会按辩题自动理解为**主张「消解人的价值」**，不需要你自己解释立场。

---

## 七、常见问题

**Q：返回 401？** 服务端目前未开启口令；若开启，加 `Authorization: Bearer <token>`。

**Q：首句要等 5 秒？** 那是级联通道（`/api/rebut`）。要更快用 `/api/rebut-omni`（首音约 1 秒）。

**Q：脚本报证书错误？** 自签证书，`curl -k` 或 `verify=False`。

**Q：为什么 `/api/rebut` 的 `duration` 限制 30–60？** 对应 30 秒与 60 秒两种发言时长；45 秒约出 250~300 字。
