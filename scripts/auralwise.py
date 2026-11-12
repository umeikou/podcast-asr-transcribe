#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AuralWise 语音转写 CLI（https://auralwise.cn，API base: https://api.auralwise.cn/v1）

API Key 解析顺序:
    1) 环境变量 AURALWISE_API_KEY
    2) 文件 ~/.config/auralwise/api_key
初始化（一次性）:
    mkdir -p ~/.config/auralwise && printf '%s' 'asr_xxx' > ~/.config/auralwise/api_key
    chmod 600 ~/.config/auralwise/api_key

子命令:
    account                     查余额 / 并发 / 限速
    pricing                     查单价
    submit  …                   提交任务，打印 task id
    status  <task_id> [--wait]  查状态（--wait 阻塞轮询至终态）
    result  <task_id> [-o f]    取结果 JSON
    run     …                   一键：提交 → 轮询 → 落结果（最常用）

提交方式:
    --audio-url URL   服务端拉取（快，不占上行带宽；要求公网可访问）
    --file PATH       本地文件走 base64（单次请求上限 200 MB）

示例:
    python3 auralwise.py run --file ep.mp3 --lang zh \
        --hotwords Omarchy,"Arch Linux",DHH --title "标题" -o result.json
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = "https://api.auralwise.cn/v1"


# ─────────────────────────── key & http ───────────────────────────
def api_key():
    k = os.environ.get("AURALWISE_API_KEY", "").strip()
    if k:
        return k
    p = os.path.expanduser("~/.config/auralwise/api_key")
    if os.path.exists(p):
        k = open(p, encoding="utf-8").read().strip()
        if k:
            return k
    raise SystemExit(
        "❌ 未找到 AuralWise API Key。\n"
        "   设置环境变量 AURALWISE_API_KEY，或执行：\n"
        "   mkdir -p ~/.config/auralwise && printf '%s' 'asr_xxx' > ~/.config/auralwise/api_key\n"
        "   chmod 600 ~/.config/auralwise/api_key"
    )


def call(method, path, body=None, timeout=120):
    url = BASE + path
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "X-API-Key": api_key(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"raw": raw.decode("utf-8", "replace")[:500]}
        hint = {
            401: "API Key 无效或已撤销",
            402: "可用额度不足（充值或换批量折扣档）",
            409: "任务仍在 processing，无法删除",
            413: "请求体过大：base64 上限 200 MB",
            429: "并发已满，稍后重试或先查 account 的 available_concurrency",
        }.get(e.code, "")
        raise SystemExit(f"❌ HTTP {e.code} {hint}\n{json.dumps(payload, ensure_ascii=False, indent=2)}")


# ─────────────────────────── 参数组装 ───────────────────────────
def build_options(a):
    """把 CLI 参数映射成 API 的 options。默认值对播客场景友好。"""
    opts = {
        "enable_asr": True,
        "enable_diarize": not a.no_diarize,
        # 播客几乎不需要声音事件检测；默认关掉省钱
        "enable_audio_events": bool(a.audio_events),
        "asr_language": None if a.lang in (None, "", "auto") else a.lang,
        "asr_beam_size": a.beam_size,
    }
    if a.mode == "standard":
        opts["optimize"] = False
    elif a.mode == "lite":
        opts["optimize"] = True
    else:
        opts["optimize"] = None          # 交给服务端按语言路由
    if a.prompt:
        opts["initial_prompt"] = a.prompt
    if a.hotwords:
        opts["hotwords"] = a.hotwords
    if not a.no_diarize:
        if a.num_speakers:
            opts["num_speakers"] = a.num_speakers
        else:
            opts["min_speakers"] = a.min_speakers
            opts["max_speakers"] = a.max_speakers
    return opts


def parse_hotwords(a):
    """--hotwords 支持逗号分隔字符串，或 @file.json（数组）。"""
    hw = a.hotwords
    if not hw:
        return None
    if len(hw) == 1 and hw[0].startswith("@"):
        path = hw[0][1:]
        data = json.load(open(path, encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("hotwords", [])
        return [str(x).strip() for x in data if str(x).strip()]
    out = []
    for item in hw:
        out += [x.strip() for x in item.split(",") if x.strip()]
    return out or None


# ─────────────────────── 提交（url / base64） ───────────────────────
def submit(a):
    opts = build_options(a)
    body = {"options": opts, "allow_training": False}

    if a.file:
        if not os.path.exists(a.file):
            raise SystemExit(f"❌ 文件不存在: {a.file}")
        size = os.path.getsize(a.file)
        if size > 200 * 1024 * 1024:
            raise SystemExit(
                f"❌ {size/1048576:.0f} MB 超过 base64 模式 200 MB 上限。\n"
                "   改用 --audio-url（先上传到可公开访问的地址），或先压缩音频。")
        print(f"📦 base64 编码 {size/1048576:.1f} MB …", flush=True)
        b64 = base64.b64encode(open(a.file, "rb").read()).decode()
        body["audio_base64"] = b64
        body["audio_filename"] = a.audio_filename or os.path.basename(a.file)
        print(f"   请求体约 {len(b64)/1048576:.0f} MB", flush=True)
    elif a.audio_url:
        body["audio_url"] = a.audio_url
        body["audio_filename"] = a.audio_filename or (
            os.path.basename(a.audio_url.split("?")[0]) or "audio.mp3")
    else:
        raise SystemExit("需要 --file 或 --audio-url")

    if a.callback_url:
        body["callback_url"] = a.callback_url
    if a.batch:
        body["batch_mode"] = True

    t0 = time.time()
    _, res = call("POST", "/tasks", body, timeout=900)
    print(f"✅ 任务已创建 {time.time()-t0:.0f}s  id={res.get('id')}  "
          f"status={res.get('status')}  size={res.get('audio_size')}")
    return res.get("id")


# ─────────────────────────── 轮询 / 取结果 ───────────────────────────
TERMINAL = ("done", "failed", "abandoned")


def wait(task_id, interval=20, max_wait=7200):
    t0 = time.time()
    while time.time() - t0 < max_wait:
        _, t = call("GET", f"/tasks/{task_id}")
        st = t.get("status")
        print(f"[{time.time()-t0:6.0f}s] {st}", flush=True)
        if st in TERMINAL:
            return st, t
        time.sleep(interval)
    return "timeout", {}


def get_result(task_id, out=None):
    _, r = call("GET", f"/tasks/{task_id}/result", timeout=300)
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False)
        n = len(r.get("segments") or [])
        b = r.get("billing") or {}
        print(f"✅ 结果已保存 {out}")
        print(f"   时长 {r.get('audio_duration')}s｜段落 {n}｜语言 {r.get('language')}"
              f"｜说话人 {r.get('num_speakers')}｜计费 {b.get('amount')} 元")
    return r


# ─────────────────────────── 子命令 ───────────────────────────
def cmd_account(_a):
    _, acc = call("GET", "/account")
    print(json.dumps(acc, ensure_ascii=False, indent=2))
    if acc.get("available_balance", 0) <= 0:
        print("\n⚠️  可用额度为 0，提交任务会返回 402。", file=sys.stderr)
    if acc.get("available_concurrency", 1) == 0:
        print("⚠️  当前无空闲并发，需等待已有任务结束。", file=sys.stderr)


def cmd_pricing(_a):
    _, p = call("GET", "/pricing")
    print(json.dumps(p, ensure_ascii=False, indent=2))
    hours = 1.6
    std = (p.get("standard_price_per_hour", 0) + p.get("diarize_price_per_hour", 0)) * hours
    lite = (p.get("zh_lite_price_per_hour", 0) + p.get("diarize_price_per_hour", 0)) * hours
    print(f"\n参考：{hours} 小时音频 → 标准档约 {std:.2f} 元 / 优化档约 {lite:.2f} 元（含说话人分离）")


def cmd_submit(a):
    tid = submit(a)
    if a.wait:
        st, _ = wait(tid, a.interval)
        if st != "done":
            raise SystemExit(f"❌ 任务终态为 {st}")
        if a.output:
            get_result(tid, a.output)


def cmd_status(a):
    _, t = call("GET", f"/tasks/{a.task_id}")
    print(json.dumps(t, ensure_ascii=False, indent=2))


def cmd_result(a):
    get_result(a.task_id, a.output)


def cmd_run(a):
    tid = submit(a)
    st, _ = wait(tid, a.interval, a.max_wait)
    if st != "done":
        raise SystemExit(f"❌ 任务终态为 {st}，未生成结果。可用 status 子命令查详情。")
    out = a.output or f"result_{tid}.json"
    get_result(tid, out)
    print(f"\nTASK_ID={tid}")


# ─────────────────────────── main ───────────────────────────
def add_submit_args(p):
    p.add_argument("--file", help="本地音频文件（走 base64）")
    p.add_argument("--audio-url", help="公网可访问的音频 URL（走服务端拉取，推荐）")
    p.add_argument("--audio-filename", help="文件名（base64 模式必填，含扩展名）")
    p.add_argument("--lang", default="zh", help="语言码，zh/en/ja/…；auto=自动检测")
    p.add_argument("--mode", default="standard", choices=["standard", "lite", "auto"],
                   help="standard=高质量词级(默认，适合中英混读/专名多)；lite=优化档省钱；auto=按语言")
    p.add_argument("--no-diarize", action="store_true", help="关闭说话人分离")
    p.add_argument("--num-speakers", type=int, help="固定说话人数（已知时更准）")
    p.add_argument("--min-speakers", type=int, default=1)
    p.add_argument("--max-speakers", type=int, default=10)
    p.add_argument("--beam-size", type=int, default=5)
    p.add_argument("--hotwords", nargs="+", help="热词，逗号分隔或用 @file.json")
    p.add_argument("--prompt", help="initial_prompt，引导风格/保留口语/提示专名")
    p.add_argument("--audio-events", action="store_true", help="开启声音事件检测（+0.1 元/小时）")
    p.add_argument("--callback-url")
    p.add_argument("--batch", action="store_true", help="批量模式（延迟至多 24h，有折扣）")
    p.add_argument("--interval", type=int, default=20, help="轮询间隔秒（受账户 tps_limit 约束）")
    p.add_argument("--max-wait", type=int, default=7200, help="最长等待秒")
    p.add_argument("-o", "--output", help="结果 JSON 输出路径")


def main():
    ap = argparse.ArgumentParser(
        description="AuralWise 语音转写（播客场景默认参数已调优）",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("account", help="查余额与并发").set_defaults(func=cmd_account)
    sub.add_parser("pricing", help="查单价").set_defaults(func=cmd_pricing)

    s = sub.add_parser("submit", help="提交任务")
    add_submit_args(s)
    s.add_argument("--wait", action="store_true")
    s.set_defaults(func=cmd_submit)

    s = sub.add_parser("status", help="查任务状态")
    s.add_argument("task_id")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("result", help="取结果")
    s.add_argument("task_id")
    s.add_argument("-o", "--output")
    s.set_defaults(func=cmd_result)

    s = sub.add_parser("run", help="提交 → 轮询 → 落结果")
    add_submit_args(s)
    s.set_defaults(func=cmd_run)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
