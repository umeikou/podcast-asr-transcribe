#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析小宇宙（xiaoyuzhoufm.com）单集页面，输出音频直链与元数据。

用法:
    python3 fetch_episode.py <episode_url> [-o episode.json] [--hotwords]

输出 JSON 字段:
    eid, title, podcast, author, duration, pduration, pub_date, pay_type,
    audio_url (优先 CDN 直链，无 302), audio_url_alt (备用), audio_size,
    mime, cover, page_url, description, shownotes, hotwords (--hotwords 时)
"""
import argparse
import json
import re
import sys
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
# 全文兜底：任意 xyzcdn / wavpub 的 mp3 链接
FALLBACK_MP3_RE = re.compile(r'https?://[^\s"\'<>]+\.(?:mp3|m4a|aac)(?:\?[^\s"\'<>]*)?')
HTML_TAG_RE = re.compile(r'<[^>]+>')


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept": "text/html,application/xhtml+xml",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace"), r.geturl()


def strip_html(s):
    if not s:
        return ""
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"</p>", "\n", s)
    s = HTML_TAG_RE.sub("", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    s = s.replace("&quot;", '"').replace("&#39;", "'")
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def guess_hotwords(title, shownotes, limit=40):
    """从标题与 shownotes 里启发式抽取候选专名（供转写热词使用）。

    规则：拉丁字母开头的专名/缩写、书名号与引号内术语、连续大写缩写。
    抽出来的是候选集，最终仍应人工/模型筛选。
    """
    text = f"{title}\n{shownotes}"
    cands = []

    # 1) 拉丁专名与缩写（Omarchy / DHH / Ruby on Rails / GDG）
    for m in re.findall(r"[A-Za-z][A-Za-z0-9\.\+\#\-]*(?:\s+[A-Z][A-Za-z0-9\.\+\#\-]*)*", text):
        w = m.strip()
        if len(w) < 2:
            continue
        if not re.search(r"[A-Z]", w):      # 必须含大写，滤掉普通英文词
            continue
        cands.append(w)

    # 2) 《》「」"" 内的中文术语
    for m in re.findall(r"[《「『“\"]([^》」』”\"]{2,20})[》」』”\"]", text):
        cands.append(m.strip())

    # 3) 中文人名式三字串（XX老师 / 主播名）——保守：只取"某某老师"前两字
    for m in re.findall(r"([\u4e00-\u9fff]{2})老师", text):
        cands.append(m + "老师")

    seen, out = set(), []
    for c in cands:
        k = c.lower()
        if k in seen or len(c) > 40:
            continue
        seen.add(k)
        out.append(c)
        if len(out) >= limit:
            break
    return out


def parse(url):
    html, final_url = fetch(url)

    m = NEXT_DATA_RE.search(html)
    if not m:
        raise SystemExit(
            "❌ 页面里没有 __NEXT_DATA__。可能是登录墙、付费内容或页面改版。\n"
            f"   最终 URL: {final_url}"
        )

    data = json.loads(m.group(1))
    ep = (data.get("props", {}).get("pageProps", {}) or {}).get("episode")
    if not ep:
        raise SystemExit("❌ __NEXT_DATA__ 中找不到 episode 节点，页面结构可能已变更。")

    media = ep.get("media") or {}
    src = media.get("source") or {}
    bak = media.get("backupSource") or {}
    enclosure = (ep.get("enclosure") or {}).get("url")

    # 优先级：备份直链(CDN，无 302) > 主链 > enclosure > 全文兜底
    audio_url = bak.get("url") or src.get("url") or enclosure
    audio_url_alt = src.get("url") or enclosure or bak.get("url")
    if not audio_url:
        found = FALLBACK_MP3_RE.findall(html)
        if found:
            audio_url = found[-1]
            audio_url_alt = found[0]

    podcast = ep.get("podcast") or {}
    shownotes = strip_html(ep.get("shownotes") or "")
    desc = ep.get("description") or ""

    dur = ep.get("duration")
    pm, ps = divmod(int(dur), 60) if dur else (0, 0)

    out = {
        "eid": ep.get("eid"),
        "title": ep.get("title"),
        "podcast": podcast.get("title"),
        "author": podcast.get("author"),
        "duration": dur,
        "duration_hms": f"{pm // 60:02d}:{pm % 60:02d}:{ps:02d}" if dur else None,
        "pub_date": ep.get("pubDate"),
        "pay_type": ep.get("payType"),
        "is_private": ep.get("isPrivateMedia"),
        "audio_url": audio_url,
        "audio_url_alt": audio_url_alt,
        "audio_size": media.get("size"),
        "mime": media.get("mimeType"),
        "cover": (ep.get("image") or {}).get("picUrl"),
        "page_url": url,
        "description": desc,
        "shownotes": shownotes,
        "ip_loc": ep.get("ipLoc"),
        "play_count": ep.get("playCount"),
    }
    out["hotwords"] = guess_hotwords(ep.get("title") or "", shownotes)
    return out


def main():
    ap = argparse.ArgumentParser(description="解析小宇宙单集页面，取得音频直链")
    ap.add_argument("url", help="形如 https://www.xiaoyuzhoufm.com/episode/<eid>")
    ap.add_argument("-o", "--output", help="写入 JSON 文件（默认打印到 stdout）")
    args = ap.parse_args()

    info = parse(args.url)

    if info["pay_type"] not in (None, "FREE"):
        print(f"⚠️  该集 payType={info['pay_type']}，可能是付费/会员内容，音频链接未必可访问。",
              file=sys.stderr)
    if info["is_private"]:
        print("⚠️  该集标记为私有媒体（isPrivateMedia=true），直链大概率需要鉴权。",
              file=sys.stderr)
    if not info["audio_url"]:
        raise SystemExit("❌ 未能提取到音频直链。")

    payload = json.dumps(info, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(payload)
        mb = (info["audio_size"] or 0) / 1048576
        print(f"✅ {info['podcast']}｜{info['title']}")
        print(f"   {info['duration_hms']}　{mb:.1f} MB　{info['pay_type']}")
        print(f"   audio_url → {info['audio_url'][:110]}")
        print(f"   已写入 {args.output}")
    else:
        print(payload)


if __name__ == "__main__":
    main()
