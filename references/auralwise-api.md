# AuralWise API 速查（实测于 2026-10）

- **Base URL**：`https://api.auralwise.cn/v1`
- **认证**：Header `X-API-Key: asr_...`（44 位，`asr_` 开头）
- **文档**：https://auralwise.cn/api-docs　**控制台**：https://auralwise.cn/settings
- **异步模型**：`POST /tasks` 立即返回 id → 轮询 `GET /tasks/:id` → `done` 后取 `GET /tasks/:id/result`

## 端点

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/tasks` | 创建转写任务 |
| POST | `/tasks/batch` | 批量提交（需 `batch_mode=true`，单次 ≤100 条 URL） |
| GET | `/tasks/:id` | 查状态 |
| GET | `/tasks/:id/result` | 取结果 |
| DELETE | `/tasks/:id` | 删任务（`processing` 时返回 409） |
| GET | `/account` | 余额、并发、限速 |
| GET | `/pricing` | 单价 |
| GET | `/audio-event-classes` | 521 类声音事件 |

## 提交字段

```json
{
  "audio_url": "公网可访问 URL（1KB~2GB）",
  "audio_base64": "或本地文件 base64（单请求 ≤200MB）",
  "audio_filename": "episode.mp3",
  "options": { ... },
  "callback_url": "可选，终态回调",
  "batch_mode": false,
  "allow_training": false
}
```

`audio_url` 与 `audio_base64` 二选一。`audio_base64` 模式**必须**给 `audio_filename` 定扩展名。

> **实测**：66 MB mp3 → base64 后 payload 88 MB，`curl --data-binary` 上传约 18 秒。能用 `audio_url` 就别用 base64，省上行带宽也省时间。

## options 完整字段

### 开关

| 字段 | 默认 | 说明 |
|---|---|---|
| `enable_asr` | true | 语音转写 |
| `enable_diarize` | true | 说话人分离（依赖 ASR，单独开为 true 会 400） |
| `enable_audio_events` | true | 声音事件检测（**播客建议关，省钱**） |
| `optimize` | null | `null`=按语言自动 / `true`=优化档 / `false`=标准档 |

### 转写档位（关键决策）

| | 优化档 `optimize=true` | 标准档 `optimize=false` |
|---|---|---|
| 速度 | 快 10–30 倍 | 基准 |
| 单价 | 0.27 元/小时 | 0.60 元/小时 |
| 时间戳 | 段级 ~100ms，**无 words** | 词/字级 ~40ms，含 `words[]` |
| 适用语言 | 中/英/西/法/葡 | 全语言 |
| 质量 | 专名略降 | 中英混读、专名、品牌名更准 |

**选择依据**：中英混读的技术/商业播客 → **标准档**（专名准确度是主要矛盾，多花几毛钱值得）。纯中文口播、只需正文 → 优化档。

### ASR 参数

| 字段 | 默认 | 建议 |
|---|---|---|
| `asr_language` | null（自动） | 已知语种就显式指定，减少误判 |
| `asr_beam_size` | 5 | 保持 5；调大更准但慢 |
| `asr_temperature` | 0.0 | 保持 0（贪心） |
| `hotwords` | null | **专名清单，收益最高的参数** |
| `initial_prompt` | null | 引导风格、保留口语、提示中英混读 |

### VAD / 分离 / 事件

| 字段 | 默认 | 说明 |
|---|---|---|
| `vad_threshold` | 0.35 | 背景音乐多时保持默认 |
| `vad_min_speech_ms` | 250 | |
| `vad_min_silence_ms` | 100 | 切分太碎可调大 |
| `no_speech_threshold` | 0.6 | 调高减少幻觉 |
| `num_speakers` | null | **已知人数就填，比自动检测准** |
| `min_speakers` / `max_speakers` | 1 / 10 | 不确定时收窄范围（如 2–6） |
| `diarize_single_speaker_threshold` | 0.05 | 低于此值判为单人 |
| `audio_events_threshold` | 0.3 | |
| `audio_events_classes` | null | null=全 521 类 |

## 结果字段

```json
{
  "task_id": "...",
  "audio_duration": 5744.399,
  "audio_md5": "20fc4f4d...",
  "language": "zh",
  "language_probability": 1.0,
  "segments": [
    {"id": 0, "start": 0.32, "end": 3.04, "text": "...",
     "speaker": "SPEAKER_0",
     "words": [{"word":"本","start":0.32,"end":0.4,"probability":0.98}]}
  ],
  "vad_segments": [...],
  "num_speakers": 4,
  "speaker_embeddings": [{"speaker_id":"SPEAKER_0","embedding":[192 维],"segment_count":281}],
  "diarize_segments": [...],
  "billing": {
    "mode": "standard", "billable_minutes": 96, "amount": 1.28, "balance": 20.44,
    "detail": {"base_price_per_hour":0.6,"base_amount":0.96,
               "diarize_price_per_hour":0.2,"diarize_amount":0.32,
               "subtotal":1.28,"total":1.28}
  }
}
```

`words[]` 仅标准档返回（优化档无此字段）。`speaker` 仅 `enable_diarize=true` 时存在。

## 计费

- 最小计费单位 1 分钟，**向上取整**（5744 秒 → 96 分钟）
- 单任务 = (基础单价 + 各附加单价) ÷ 60 × 计费分钟
- 批量模式再乘 `batch_discount`（当前 0.7）
- 提交时按预估值冻结 `held_amount`，完成结算、失败释放
- 2026-10 实测单价：标准 0.6 / 优化 0.27 / 分离 +0.2 / 声音事件 +0.1（元/小时）

## 限额与错误

| 项 | 值 |
|---|---|
| URL 模式文件 | 1 KB ~ 2 GB |
| base64 模式 | 单请求 ≤ 200 MB |
| 时长上限 | 5 小时 |
| 限速 | **所有 `/v1` 请求共用一个 tps 额度**，tier1 账户 `tps_limit=1`、`tps_burst=5` |
| 并发 | tier1 为 3，批量任务不占并发 |

| 码 | 含义 | 处理 |
|---|---|---|
| 400 | 参数错（如 diarize 开而 asr 关、三项开关全 false） | 改参数 |
| 401 | Key 无效 | 查 key |
| 402 | 可用额度不足 | 充值，或转批量模式拿折扣 |
| 409 | 任务 processing 中不可删除 | 等终态 |
| 413 | 请求体过大 | 转 `audio_url` 模式 |
| 429 | 并发已满 | 查 `/account` 的 `available_concurrency`，退避重试 |

**轮询间隔**：tier1 `tps_limit=1`，建议 ≥20 秒；文档建议 3–5 秒仅适用于高 tier 账户。频繁轮询会吃满全局限速，反而拖慢。

## 实测耗时（1 小时 35 分标准档 + 分离）

提交 18 秒 → 处理 **8 分钟** → 结果 JSON 2.5 MB。优化档预计 1 分钟内。
