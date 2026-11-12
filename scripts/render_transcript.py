#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 AuralWise 结果 JSON 渲染成可读转写稿（md / txt / srt），并附带质检工具。

用法:
    # 1) 先体检：说话人分布 + 英文词频（用来发现专名误识别）
    python3 render_transcript.py result.json --probe

    # 2) 再出稿
    python3 render_transcript.py result.json \
        --title "标题" --podcast "节目名" \
        --corrections corrections.json \
        --speakers speakers.json \
        -o ./out

产物:
    out/<标题>.md      带时间戳与说话人的正文 + 说话人对照表
    out/<标题>.txt     纯正文，供摘要/检索
    out/<标题>.srt     字幕，含说话人标记

corrections.json 支持两种写法:
    [["Omachi","Omarchy"], ["科脑","内核"]]
    {"Omachi":"Omarchy", "科脑":"内核"}
长串请放在前面（脚本会按长度降序自动排序，避免短串先命中）。

speakers.json:
    {"SPEAKER_0": "朱峰", "SPEAKER_1": "嘉宾A"}
"""
import argparse
import collections
import json
import os
import re
import sys

CJK = r"\u4e00-\u9fff"

# 可安全自动处理的中英混排清洗（不需要人工判断的机械规则）
MECHANICAL = [
    # 单字母被空格拆开：A I → AI, M C P → MCP, G D G → GDG
    (re.compile(r"\b([A-Za-z])(?:\s+([A-Za-z])){1,5}\b"), None),   # 见 normalize_single_letters
    (re.compile(r"[ \t]{2,}"), " "),
]


def normalize_single_letters(t):
    """把 "A I" / "M C P" / "O S" 这类被空格拆散的单字母序列合并。

    只在整串都是单字母且有 2~6 个时合并，避免误伤 "a b test" 这类正常内容。
    """
    def repl(m):
        letters = re.findall(r"[A-Za-z]", m.group(0))
        if 2 <= len(letters) <= 6:
            return "".join(letters)
        return m.group(0)
    return re.sub(r"(?<![A-Za-z])(?:[A-Za-z]\s+){1,5}[A-Za-z](?![A-Za-z])", repl, t)


def load_corrections(path):
    """读取校正表。支持 dict / 列表两两种写法，允许 `_` 开头的注释键。"""
    if not path:
        return []
    data = json.load(open(path, encoding="utf-8"))
    if isinstance(data, dict):
        raw = data.items()
    else:
        raw = ((x[0], x[1]) for x in data if isinstance(x, (list, tuple)) and len(x) == 2)

    pairs = []
    for k, v in raw:
        # 跳过注释键与非字符串值（如 "_说明": "..."、"_留疑示例": [...]）
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        if k.startswith("_"):
            continue
        pairs.append((k, v))
    # 长串优先，防止短串先命中造成半截替换
    return sorted(pairs, key=lambda p: -len(p[0]))


def clean(t, pairs):
    # 两遍校正：第一遍处理带空格的原始形态（如 "A G N A"→"Agent"），
    # 第二遍处理合并后的形态（如 "AGNA"→"Agent"），两种写法都能命中。
    for a, b in pairs:
        if a in t:
            t = t.replace(a, b)
    t = normalize_single_letters(t)
    for a, b in pairs:
        if a in t:
            t = t.replace(a, b)
    t = re.sub(rf"(?<=[{CJK}])\s+(?=[{CJK}])", "", t)     # 中文间多余空格
    t = re.sub(r"[ \t]{2,}", " ", t).strip()
    t = re.sub(rf"(?<=[{CJK}]),", "，", t)                # 中文后半角标点
    t = re.sub(rf"(?<=[{CJK}]);", "；", t)
    t = re.sub(rf"(?<=[{CJK}]):", "：", t)
    t = re.sub(rf"(?<=[{CJK}])\.(?=\s|$)", "。", t)
    t = re.sub(rf"(?<=[{CJK}])\?(?=\s|$)", "？", t)
    t = re.sub(rf"(?<=[{CJK}])!(?=\s|$)", "！", t)
    return t


def hms(sec, always_hours=False):
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h or always_hours:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def srt_time(sec):
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def probe(r, top_en=60):
    """体检输出：说话人分布 + 英文词频 + 疑似空格拆散的缩写。"""
    segs = r.get("segments") or []
    if not segs:
        print("⚠️ 结果里没有 segments。检查是否只开了声音事件检测。")
        return

    cnt = collections.Counter()
    dur = collections.Counter()
    chars = collections.Counter()
    for s in segs:
        sp = s.get("speaker") or "UNKNOWN"
        cnt[sp] += 1
        dur[sp] += max(0.0, s.get("end", 0) - s.get("start", 0))
        chars[sp] += len(s.get("text") or "")
    tot = sum(dur.values()) or 1

    print(f"总时长 {hms(r.get('audio_duration', 0), True)}｜段落 {len(segs)}"
          f"｜语言 {r.get('language')}｜说话人 {r.get('num_speakers')}")
    print("\n说话人分布:")
    for sp, _ in dur.most_common():
        print(f"  {sp:12s} {cnt[sp]:4d} 段  {dur[sp]/60:6.1f} 分  "
              f"{dur[sp]/tot*100:5.1f}%  {chars[sp]:6d} 字")

    text = "\n".join(s.get("text") or "" for s in segs)
    words = re.findall(r"[A-Za-z][A-Za-z0-9\.\-]{1,}", text)
    freq = collections.Counter(words)
    print(f"\n英文词频（top {top_en}，低频与拼写异常项重点看）:")
    line = []
    for w, n in freq.most_common(top_en):
        line.append(f"{n:3d} {w}")
        if len(line) == 4:
            print("  " + "   ".join(line))
            line = []
    if line:
        print("  " + "   ".join(line))

    single = re.findall(r"(?<![A-Za-z])(?:[A-Za-z]\s+){1,5}[A-Za-z](?![A-Za-z])", text)
    if single:
        c = collections.Counter(single)
        print(f"\n⚠️  发现 {len(single)} 处单字母被空格拆散（渲染时会自动合并）:")
        for k, v in c.most_common(8):
            print(f"   {v:3d} × {k!r}")

    b = r.get("billing") or {}
    if b:
        print(f"\n计费 {b.get('amount')} 元｜{b.get('mode')}｜{b.get('billable_minutes')} 分钟"
              f"｜余额 {b.get('balance')}")


def render(r, pairs, names, title, podcast, outdir):
    segs = r.get("segments") or []
    merged = []
    for s in segs:
        sp = s.get("speaker") or "UNKNOWN"
        tx = clean(s.get("text") or "", pairs)
        if merged and merged[-1]["sp"] == sp:
            merged[-1]["end"] = s.get("end", merged[-1]["end"])
            merged[-1]["text"] += tx
        else:
            merged.append({"sp": sp, "start": s.get("start", 0),
                           "end": s.get("end", 0), "text": tx})

    dur = collections.Counter()
    cnt = collections.Counter()
    for s in segs:
        sp = s.get("speaker") or "UNKNOWN"
        dur[sp] += max(0.0, s.get("end", 0) - s.get("start", 0))
        cnt[sp] += 1
    tot = sum(dur.values()) or 1

    os.makedirs(outdir, exist_ok=True)
    base = re.sub(r'[/\\:*?"<>|\r\n\t]', "-", title or "transcript")[:80].strip(" .-")

    # ── markdown ──
    L = [f"# {title}", ""]
    meta = []
    if podcast:
        meta.append(f"**节目**：{podcast}")
    meta.append(f"**时长**：{hms(r.get('audio_duration', 0), True)}")
    L.append("　|　".join(meta))
    L.append("")
    b = r.get("billing") or {}
    L.append(f"**转写**：AuralWise（{b.get('mode', '—')}）　|　"
             f"**识别语言**：{r.get('language')}"
             f"（置信度 {(r.get('language_probability') or 0):.1%}）　|　"
             f"**说话人**：{r.get('num_speakers')} 人")
    L += ["", "---", "", "## 说话人对照", "",
          "| 标签 | 名称 | 段数 | 发言时长 | 占比 |", "|---|---|---|---|---|"]
    for sp, _ in dur.most_common():
        nm = names.get(sp, "（待确认）")
        L.append(f"| {sp} | {nm} | {cnt[sp]} | {dur[sp]/60:.1f} 分 | {dur[sp]/tot:.1%} |")
    L += ["", "> 说话人为声纹聚类结果，标签顺序不代表出场顺序；"
              "标「待确认」者未能从音频内容唯一确定身份，请勿臆测填名。", "",
          "---", "", "## 转写正文", ""]

    for m in merged:
        nm = names.get(m["sp"], m["sp"])
        L.append(f"**[{hms(m['start'], True)}] {nm}**：{m['text']}")
        L.append("")
    md_path = os.path.join(outdir, base + ".md")
    open(md_path, "w", encoding="utf-8").write("\n".join(L))

    # ── 纯文本 ──
    plain = "\n".join(clean(s.get("text") or "", pairs) for s in segs)
    txt_path = os.path.join(outdir, base + ".txt")
    open(txt_path, "w", encoding="utf-8").write(plain)

    # ── SRT ──
    srt_path = os.path.join(outdir, base + ".srt")
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, s in enumerate(segs, 1):
            sp = s.get("speaker") or ""
            tag = names.get(sp, sp)
            f.write(f"{i}\n{srt_time(s.get('start', 0))} --> "
                    f"{srt_time(s.get('end', 0))}\n[{tag}] "
                    f"{clean(s.get('text') or '', pairs)}\n\n")

    print(f"✅ {md_path}")
    print(f"   {txt_path}  （{len(plain)} 字）")
    print(f"   {srt_path}  （{len(segs)} 条）")
    print(f"   段落 {len(segs)} → 合并后 {len(merged)}")
    return md_path


def main():
    ap = argparse.ArgumentParser(description="渲染 AuralWise 转写结果")
    ap.add_argument("result", help="GET /tasks/:id/result 保存的 JSON")
    ap.add_argument("--title", default=None)
    ap.add_argument("--podcast", default=None)
    ap.add_argument("--corrections", help="校正表 JSON")
    ap.add_argument("--speakers", help="说话人姓名映射 JSON")
    ap.add_argument("--probe", action="store_true", help="只做体检，不出稿")
    ap.add_argument("--top-en", type=int, default=60)
    ap.add_argument("-o", "--outdir", default=".")
    a = ap.parse_args()

    r = json.load(open(a.result, encoding="utf-8"))
    if a.probe:
        probe(r, a.top_en)
        return

    pairs = load_corrections(a.corrections)
    names = {}
    if a.speakers and os.path.exists(a.speakers):
        names = json.load(open(a.speakers, encoding="utf-8"))
    title = a.title or (os.path.splitext(os.path.basename(a.result))[0])
    render(r, pairs, names, title, a.podcast, a.outdir)


if __name__ == "__main__":
    main()
