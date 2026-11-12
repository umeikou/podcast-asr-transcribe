#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载播客音频：断点续传 + 字节数校验 + 文件名净化。

用法:
    python3 download_audio.py --info episode.json [--dir ./podcast]
    python3 download_audio.py --url <mp3直链> --name "标题" --size 69191086 --dir ./podcast

设计要点:
- 用 -C 续传语义（Range 请求），中断后可重跑，不会重复占带宽。
- 以服务器 Content-Length 与页面 media.size 双重校验，避免半截文件被当成成功。
- 文件名去掉 / : * ? " < > | 等跨平台非法字符，并限制长度。
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
ILLEGAL = r'[/\\:*?"<>|\r\n\t]'


def safe_name(s, maxlen=90):
    s = re.sub(ILLEGAL, "-", s or "audio")
    s = re.sub(r"\s+", " ", s).strip(" .-")
    if len(s) > maxlen:
        s = s[:maxlen].rstrip(" .-")
    return s or "audio"


def head_size(url):
    """取远端文件大小；失败返回 None。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA}, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            n = r.headers.get("Content-Length")
            return int(n) if n else None
    except Exception:
        return None


def download(url, dest, expected=None, retries=3):
    """带续传的下载。返回最终字节数。"""
    total = expected or head_size(url)
    if total and os.path.exists(dest) and os.path.getsize(dest) == total:
        print(f"⏭  已存在且大小一致，跳过下载：{dest}")
        return total

    for attempt in range(1, retries + 1):
        have = os.path.getsize(dest) if os.path.exists(dest) else 0
        if total and have >= total:
            break
        headers = {"User-Agent": UA}
        if have:
            headers["Range"] = f"bytes={have}-"
            print(f"↻  续传，已有 {have/1048576:.1f} MB")
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                # 服务器忽略 Range 时会返回 200 + 全量，需要重置
                if have and r.status == 200:
                    have = 0
                    mode = "wb"
                else:
                    mode = "ab"
                with open(dest, mode) as f:
                    got = have
                    last = time.time()
                    while True:
                        chunk = r.read(262144)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        if time.time() - last > 3:
                            pct = f"{got/total*100:.0f}%" if total else "?"
                            print(f"   {got/1048576:.1f} MB / "
                                  f"{total/1048576:.1f} MB ({pct})", flush=True)
                            last = time.time()
        except urllib.error.HTTPError as e:
            if e.code == 416:       # Range 越界：本地已完整
                break
            print(f"⚠️  HTTP {e.code}（第 {attempt} 次）", file=sys.stderr)
            time.sleep(2)
            continue
        except Exception as e:
            print(f"⚠️  {type(e).__name__}: {e}（第 {attempt} 次）", file=sys.stderr)
            time.sleep(2)
            continue

        size = os.path.getsize(dest)
        if not total or size >= total:
            return size
        print(f"⚠️  不完整：{size}/{total}，重试", file=sys.stderr)

    final = os.path.getsize(dest) if os.path.exists(dest) else 0
    if total and final != total:
        raise SystemExit(f"❌ 下载不完整：{final} / {total} 字节。可重跑本脚本续传。")
    return final


def main():
    ap = argparse.ArgumentParser(description="下载播客音频并校验完整性")
    ap.add_argument("--info", help="fetch_episode.py 输出的 JSON")
    ap.add_argument("--url")
    ap.add_argument("--name")
    ap.add_argument("--size", type=int, default=None)
    ap.add_argument("--dir", default=".", help="保存目录")
    ap.add_argument("--ext", default=".mp3")
    args = ap.parse_args()

    if args.info:
        info = json.load(open(args.info, encoding="utf-8"))
        url = info["audio_url"]
        name = f"{info.get('podcast') or ''} - {info.get('title') or ''}".strip(" -")
        size = info.get("audio_size")
        ext = os.path.splitext((url or "").split("?")[0])[1] or args.ext
    elif args.url:
        url, name, size = args.url, args.name or "audio", args.size
        ext = os.path.splitext(url.split("?")[0])[1] or args.ext
    else:
        raise SystemExit("需要 --info 或 --url")

    os.makedirs(args.dir, exist_ok=True)
    dest = os.path.join(args.dir, safe_name(name) + ext)

    print(f"⬇️  {os.path.basename(dest)}")
    n = download(url, dest, size)
    print(f"✅ 完成 {n/1048576:.1f} MB → {dest}")

    # 落一份元信息，供后续渲染使用
    meta = os.path.join(args.dir, safe_name(name) + ".meta.json")
    if args.info:
        with open(meta, "w", encoding="utf-8") as f:
            json.dump({**json.load(open(args.info, encoding="utf-8")),
                       "local_file": dest, "local_size": n}, f,
                      ensure_ascii=False, indent=2)
        print(f"   元信息 → {meta}")
    print(dest)


if __name__ == "__main__":
    main()
