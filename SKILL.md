---
name: podcast-asr-transcribe
description: 把播客/音频下载下来并转写成带时间戳与说话人的文字稿。当用户给出小宇宙（xiaoyuzhoufm.com）等播客链接并说"下载这期音频""把这期播客转成文字""要逐字稿/字幕/说话人区分"，或给出本地音频要求转写时使用。覆盖抓取页面拿直链、断点续传下载、AuralWise 语音转写 API 提交与轮询、专名校正、生成 md/txt/srt 全流程。
agent_created: true
---

# 播客下载与语音转写

## 这个 skill 解决什么

把"一个播客链接"变成"一份可交付的文字稿"，中间有四个容易踩坑的环节：

1. **拿直链** —— 页面是 Next.js 渲染，直链藏在 `__NEXT_DATA__` 里，还有 302 跳板和备份 CDN
2. **下载校验** —— 大文件半截成功、文件名含非法字符
3. **调转写 API** —— 异步任务、参数选档、限速、计费
4. **校正与渲染** —— 原始 ASR 输出不能直接交付，专名误识别必须处理

四个环节各有一个脚本，按顺序跑即可。

## 环境准备

API Key 解析顺序：环境变量 `AURALWISE_API_KEY` → `~/.config/auralwise/api_key`。

```bash
mkdir -p ~/.config/auralwise
printf '%s' 'asr_xxx' > ~/.config/auralwise/api_key
chmod 600 ~/.config/auralwise/api_key
```

验证连通与余额：

```bash
python3 scripts/auralwise.py account
```

余额不足提交会返回 402；并发占满返回 429。先查再提交，避免白等。

---

## 工作流

下文以 `$S` 指代此 skill 的 `scripts/` 目录。

### 第 1 步｜拿到音频直链

```bash
python3 $S/fetch_episode.py "https://www.xiaoyuzhoufm.com/episode/xxxx" -o ep.json
```

关键字段：`audio_url`（已优选 CDN 直链）、`audio_size`（精确字节数）、`duration_hms`、`pay_type`、`hotwords`（从 shownotes 抽的候选热词）。

**检查三件事**：

- `pay_type` 不是 `FREE` → 付费集，直链大概率不可用，先告知用户
- `audio_size` 有值 → 后面下载要拿它对账
- `hotwords` 是候选集，**需筛一遍**，会混进 `Blog`、`IT` 这类无用词

页结构与字段含义详见 `references/xiaoyuzhou.md`。

### 第 2 步｜下载并校验

```bash
python3 $S/download_audio.py --info ep.json --dir ./podcast
```

断点续传、按 `media.size` 双向校验，并把合并后的元信息写到 `<名称>.meta.json`。中断后重跑不会重复占带宽。

**若音频已在本地**，跳过第 1、2 步。

### 第 3 步｜提交转写

**优先用 `--audio-url`**（服务端拉取，不占上行带宽）：

```bash
python3 $S/auralwise.py run \
  --audio-url "https://media.xyzcdn.net/....mp3" \
  --lang zh \
  --hotwords @ep.json \
  --prompt "这是一档中文科技播客……（见下方策略）" \
  -o result.json
```

**本地文件**走 base64（`--file`），单请求上限 200 MB。66 MB 的 mp3 约 88 MB payload，上传约 20 秒。

`run` 自动完成提交 → 轮询 → 落结果。长任务（1.6 小时音频标准档约 8 分钟）应后台运行，不要阻塞对话。

#### 参数怎么选

| 场景 | 参数 |
|---|---|
| **中英混读 / 专名多 / 技术播客** | `--mode standard`（默认），词级时间戳，专名最准 |
| 纯中文口播、只要正文、追求便宜快 | `--mode lite`（快 10–30 倍，单价 0.27 vs 0.60 元/小时） |
| 已知人数 | 加 `--num-speakers N`，比自动检测准 |
| 人数不确定 | `--min-speakers 2 --max-speakers 6` |
| 语种明确 | 显式 `--lang zh`，别用 `auto`，减少误判 |
| 播客 | **不要加 `--audio-events`**（默认关），对播客无用且加价 |

成本参考：1.6 小时音频标准档 + 分离 = **1.28 元**。提交前可用 `auralwise.py pricing` 估算。

#### hotwords 与 prompt 策略

`--hotwords @ep.json` 直接读 `fetch_episode.py` 输出的候选集。也可手动给：

```bash
--hotwords Omarchy,"Arch Linux",Hyprland,DHH,"Ruby on Rails"
```

`--prompt` 写一句"场景 + 语种 + 期望行为"，具体到领域和专名，不要写"请准确转写"这种空话：

> 这是一档中文科技播客，主持人与嘉宾围绕操作系统与 AI 展开讨论。对话中频繁夹杂英文技术名词，如 Omarchy、Arch Linux、Hyprland、DHH、MCP 等，请准确保留中英混读的原始表达。

### 第 4 步｜先体检，再出稿

**不要跳过这一步。** 直接渲染只会得到一份没救过的粗稿。

```bash
python3 $S/render_transcript.py result.json --probe
```

输出说话人分布、英文词频表、单字母拆散告警。**逐条扫低频英文词**——这是发现 `Omachi`→`Omarchy`、`Omab`→`Omakub` 这类误识的唯一有效手段。

### 第 5 步｜写校正表并渲染

```bash
python3 $S/render_transcript.py result.json \
  --title "单集标题" --podcast "节目名" \
  --corrections corrections.json \
  --speakers speakers.json \
  -o ./podcast
```

产出 `<标题>.md`（带时间戳正文 + 说话人对照表）、`.txt`（纯正文）、`.srt`（字幕）。

**校正的硬规则**：

- 只改**语义可推理**的错（`科脑`→`内核` 因为语境是 Linux；`三十七三针纳斯`→`37signals` 因为数字形近）
- **绝不硬猜**无法确认的产品名/人名，保留原文并标 `[存疑]`，交给用户判断
- 用户要的不是"改得多"，是"不改错"

**说话人姓名映射**：

- 可确认依据：自我介绍、他人称呼、职能自述、shownotes 的主播名单
- 无法唯一确定时，**标成"嘉宾A / 嘉宾B"并注明原因**，不要分配姓名
- 陷阱：对话里出现"老高说……"说明**说话人不是老高**

校正方法论、常见误识模式、机洗规则详见 `references/asr-quality.md`；校正表样例见 `assets/corrections.example.json`。

---

## 交付时要说明什么

1. 音频参数（时长/大小/码率）与校验结果
2. 转写配置与计费（哪一档、多少钱、余额剩多少）
3. 做了哪些校正、改了多少处
4. **哪些没敢改**，标了 `[存疑]` 的具体词
5. 说话人映射中确定与不确定的部分

第 4、5 项不能省——这是让用户敢用这份稿子的前提。

## 常见故障

| 现象 | 处理 |
|---|---|
| 页面无 `__NEXT_DATA__` | 登录墙/付费墙/改版，告知用户 |
| 直链 403 或文件极小 | 付费试听片段，`pay_type` 非 FREE |
| 提交返回 413 | 改 `--audio-url`，或先压缩音频 |
| 提交返回 402 | 余额不足，或转批量模式拿 7 折 |
| 429 | 查 `account` 的 `available_concurrency`，退避重试 |
| 任务 `failed` | 多半是服务端拉不到 URL，改用 `--file` 走 base64 |
| 轮询过于频繁被限速 | tier1 账户 `tps_limit=1`，间隔设 ≥20 秒 |

API 全量参数与错误码见 `references/auralwise-api.md`。

## 资源

| 文件 | 内容 |
|---|---|
| `scripts/fetch_episode.py` | 解析小宇宙页面，输出直链 + 元数据 + 候选热词 |
| `scripts/download_audio.py` | 断点续传下载 + 字节校验 + 文件名净化 |
| `scripts/auralwise.py` | 转写 CLI：account/pricing/submit/status/result/run |
| `scripts/render_transcript.py` | 体检与渲染 md/txt/srt |
| `references/xiaoyuzhou.md` | 小宇宙页面结构、直链优先级、失败情形 |
| `references/auralwise-api.md` | API 端点、options 全字段、计费、错误码 |
| `references/asr-quality.md` | 校正方法论、说话人映射、热词策略 |
| `assets/corrections.example.json` | 校正表与说话人映射样例 |
