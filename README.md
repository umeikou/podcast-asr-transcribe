# podcast-asr-transcribe

把一个播客链接变成一份带时间戳和说话人的文字稿。这是一个 Agent Skill：放进 Kiro 等支持 skill 的 AI 编程助手的 skills 目录后，对助手说"把这期播客转成文字"，它会按 `SKILL.md` 里的流程自动执行。四个脚本也可以脱离助手，直接在命令行使用。

- 输入：小宇宙（xiaoyuzhoufm.com）单集链接，或本地音频文件
- 输出：`.md`（带时间戳正文和说话人对照表）、`.txt`（纯正文）、`.srt`（字幕）
- 转写服务：[AuralWise](https://auralwise.cn) 语音转写 API（付费，按音频时长计费）

## 环境要求

- Python 3，只用标准库，无需 `pip install`
- 一个 AuralWise API Key（申请方法见下文）

## 安装

把仓库克隆到你的 skills 目录。以 Kiro 为例：

```bash
git clone https://github.com/umeikou/podcast-asr-transcribe.git ~/.kiro/skills/podcast-asr-transcribe
```

只想用命令行的话，克隆到任意目录即可。

## 申请 AuralWise API Key

1. 打开 [auralwise.cn](https://auralwise.cn)，注册并登录账号。
2. 进入控制台的设置页 [auralwise.cn/settings](https://auralwise.cn/settings)，创建 API Key。Key 以 `asr_` 开头，共 44 位。
3. 给账户充值。转写按时长扣费，余额不足时提交任务会返回 402。
4. 把 Key 保存到本机：

   ```bash
   mkdir -p ~/.config/auralwise
   printf '%s' 'asr_你的Key' > ~/.config/auralwise/api_key
   chmod 600 ~/.config/auralwise/api_key
   ```

   也可以改用环境变量 `export AURALWISE_API_KEY=asr_你的Key`。脚本先读环境变量，读不到再读上面的文件。

5. 验证 Key 和余额：

   ```bash
   python3 scripts/auralwise.py account
   ```

   能看到余额和并发数就说明配置成功。返回 401 表示 Key 无效。

控制台页面的具体按钮名称以 AuralWise 官网为准。API 完整说明见 [AuralWise API 文档](https://auralwise.cn/api-docs)。

**不要把 API Key 提交到 Git 仓库。** 本仓库的 `.gitignore` 已排除 `api_key`、`.env`、音频和转写结果文件。

## 费用参考

以下单价为 2026 年 10 月实测，以官网 `pricing` 接口为准（`python3 scripts/auralwise.py pricing`）：

| 项目 | 单价（元/小时音频） |
|---|---|
| 标准档转写（词级时间戳，专名更准） | 0.60 |
| 优化档转写（快 10 到 30 倍，段级时间戳） | 0.27 |
| 说话人分离 | +0.20 |

实例：一期 1 小时 36 分钟的播客，标准档加说话人分离，扣费 1.28 元，处理约 8 分钟。

## 工作流程

```text
播客链接
  │ 1. fetch_episode.py    解析页面，拿到音频直链、时长、大小、候选热词
  ▼
ep.json
  │ 2. download_audio.py   断点续传下载，按字节数校验（可选，已有本地音频可跳过）
  ▼
音频文件
  │ 3. auralwise.py run    提交转写 → 轮询状态 → 保存结果
  ▼
result.json
  │ 4. render_transcript.py --probe   体检：说话人分布、英文词频、疑似误识
  ▼
人工写校正表 corrections.json 和说话人表 speakers.json
  │ 5. render_transcript.py           套用校正，输出文字稿
  ▼
标题.md / 标题.txt / 标题.srt
```

### 1. 拿到音频直链

```bash
python3 scripts/fetch_episode.py "https://www.xiaoyuzhoufm.com/episode/xxxx" -o ep.json
```

`ep.json` 里要检查三项：`pay_type` 不是 `FREE` 说明是付费集，直链大概率不可用；`audio_size` 用于下载后对账；`hotwords` 是从节目简介里抽出的候选专名，需要删掉 `Blog`、`IT` 这类没用的词。

### 2. 下载音频（可选）

```bash
python3 scripts/download_audio.py --info ep.json --dir ./podcast
```

中断后重跑会从断点继续。如果第 3 步用 `--audio-url` 让服务端直接拉取，这一步可以跳过。

### 3. 提交转写

```bash
python3 scripts/auralwise.py run \
  --audio-url "音频直链" \
  --lang zh \
  --hotwords @ep.json \
  --prompt "这是一档中文科技播客，对话中夹杂英文技术名词，如 Arch Linux、MCP 等，请保留中英混读的原始表达。" \
  -o result.json
```

- 本地文件改用 `--file 路径`，单个请求上限 200 MB。
- 已知说话人数时加 `--num-speakers N`，比自动检测准。
- 纯中文、只要正文、想省钱时加 `--mode lite`（优化档）。
- 长音频要几分钟，建议放到后台运行。

### 4. 体检

```bash
python3 scripts/render_transcript.py result.json --probe
```

逐条看低频英文词，这是发现专名误识（例如 `Omachi` 应为 `Omarchy`）最有效的办法。

### 5. 校正并出稿

参照 `assets/corrections.example.json` 写校正表和说话人姓名映射，然后：

```bash
python3 scripts/render_transcript.py result.json \
  --title "单集标题" --podcast "节目名" \
  --corrections corrections.json \
  --speakers speakers.json \
  -o ./podcast
```

校正原则：只改能从上下文确定的错误；确认不了的专名保留原文并标 `[存疑]`；说话人身份无法唯一确定时标"嘉宾A / 嘉宾B"，不硬猜姓名。

## 常见问题

| 现象 | 原因与处理 |
|---|---|
| 401 | API Key 无效，检查 Key 是否完整、是否多了空格或换行 |
| 402 | 余额不足，先充值 |
| 413 | 请求体过大，改用 `--audio-url`，或先压缩音频 |
| 429 | 并发或请求频率超限，用 `account` 查可用并发后稍等重试 |
| 任务状态为 failed | 多半是服务端拉不到音频链接，改用 `--file` 上传本地文件 |
| 页面解析失败 | 单集有登录墙、付费墙，或小宇宙页面改版 |

## 目录结构

| 路径 | 内容 |
|---|---|
| `SKILL.md` | 给 AI 助手读的完整操作说明 |
| `scripts/fetch_episode.py` | 解析小宇宙页面，输出直链、元数据、候选热词 |
| `scripts/download_audio.py` | 断点续传下载、字节校验、文件名净化 |
| `scripts/auralwise.py` | 转写命令行：account / pricing / submit / status / result / run |
| `scripts/render_transcript.py` | 体检，渲染 md / txt / srt |
| `references/xiaoyuzhou.md` | 小宇宙页面结构、直链优先级、失败情形 |
| `references/auralwise-api.md` | API 端点、全部参数、计费、错误码 |
| `references/asr-quality.md` | 校正方法、说话人映射、热词策略 |
| `assets/corrections.example.json` | 校正表与说话人映射样例 |
