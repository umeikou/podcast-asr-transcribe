# 小宇宙页面结构与音频直链提取

## 结论先行

单集页 `https://www.xiaoyuzhoufm.com/episode/<eid>` 的 HTML 内嵌 `__NEXT_DATA__` JSON，**所有需要的字段都在里面**，不需要抓 App API、不需要登录。

```
props.pageProps.episode
```

## 关键字段

| 字段 | 说明 |
|---|---|
| `title` | 单集标题 |
| `description` | 纯文本简介 |
| `shownotes` | **HTML 版完整 shownotes，比 description 全得多**（可能是 5 倍长度，含嘉宾名单、活动、赞助） |
| `duration` | 整数秒 |
| `pubDate` | ISO 8601 |
| `payType` | `FREE` / 其他。**非 FREE 大概率拿不到音频** |
| `isPrivateMedia` | true 时直链需鉴权 |
| `media.size` | **精确字节数，用于下载校验** |
| `media.mimeType` | |
| `media.source.url` | 主链，常见 `tk.wavpub.com/xxx.mp3` |
| `media.backupSource.url` | **备份链，通常是 `media.xyzcdn.net` CDN，无 302，优先用** |
| `transcriptMediaId` | 形如 `<pid>/<hash>.mp3`，拼 `https://media.xyzcdn.net/` 即 CDN 直链 |
| `podcast.title` / `author` | 节目名 / 作者 |
| `image.picUrl` | 封面 |
| `enclosure.url` | 兜底，通常等于 `media.source.url` |

## 直链优先级

```
media.backupSource.url  (media.xyzcdn.net，直达，首选)
  > media.source.url    (tk.wavpub.com，302 跳到这里)
  > enclosure.url
  > 全文正则兜底 (mp3|m4a|aac)
```

**已验证**：`tk.wavpub.com` 与 `media.xyzcdn.net` 指向**同一文件**（Content-Length 完全一致），不是两段音频，无需拼接广告片段。

## 请求伪装

普通 `curl` 默认 UA 会被拦或拿到不完整页面。必须带浏览器 UA：

```
User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36
Accept-Language: zh-CN,zh;q=0.9
```

## 音频下载

- CDN 支持 `Range`，可 `-C -` 续传
- 用 `media.size` 与 `Content-Length` **双向校验**，避免半截文件冒充成功（实测 66 MB 文件 5 秒下完，但仍要校验）
- 音频通常是 MP3 96 kbps / 44.1 kHz / 立体声

## 从 shownotes 抽热词

`shownotes` 是热词的最佳来源，因为主播自己会把专名写进去。可提取：

1. **拉丁专名**：`Omarchy`、`Ruby on Rails`、`DHH`、`Hyprland`
2. **全大写缩写**：`PGPU`、`PCDN`、`GDG`、`MCP`、`API`
3. **《》「」引号内术语**
4. **"XX老师"前的中文名**

`fetch_episode.py --hotwords` 会输出候选集，但要**人工/模型筛一遍**——会混进 `Blog`、`IT`、`Spotify` 这类无用项。

## 失败情形

| 现象 | 原因 |
|---|---|
| 页面无 `__NEXT_DATA__` | 登录墙 / 付费墙 / 页面改版 |
| 有 JSON 但无 `episode` | 结构变更 |
| 有直链但下载 403 | 私有媒体，`isPrivateMedia=true` |
| 有直链但下载很小 | 付费集试听片段 |
