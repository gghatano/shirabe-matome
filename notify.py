#!/usr/bin/env python3
"""その日のドラフト一覧を Discord に通知する。

    python notify.py --date 2026-07-27
    python notify.py --date 2026-07-27 --channel 123456789

チャンネルの決め方は次の順。
    1. --channel
    2. 環境変数 SHIRABE_DISCORD_CHANNEL
    3. 会話ログに残っている直近の Discord チャンネル

チャンネル ID はリポジトリに置かない（public なので）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DRAFTS_DIR = ROOT / "drafts"
PROJECTS_DIR = Path.home() / ".claude" / "projects"
DISCORD_ENV = Path.home() / ".claude" / "channels" / "discord" / ".env"
JST = timezone(timedelta(hours=9))

# Discord の 1 メッセージ上限は 2000 文字。余裕をみて切る。
MAX_MSG = 1900


def token() -> str:
    if not DISCORD_ENV.is_file():
        sys.exit(f"エラー: {DISCORD_ENV} がありません")
    for line in DISCORD_ENV.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("DISCORD_BOT_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("エラー: DISCORD_BOT_TOKEN が見つかりません")


def newest_channel() -> str | None:
    """会話ログに残っている直近の Discord チャンネル ID を拾う。

    JSONL の生の行では `chat_id=\\"123\\"` のようにクォートがエスケープされている。
    行そのものに正規表現をかけると一致しないので、必ず JSON を解いてから探す。
    """
    best_ts, best_id = "", None
    pat = re.compile(r'<channel\s+[^>]*chat_id="(\d+)"')
    if not PROJECTS_DIR.is_dir():
        return None
    for path in PROJECTS_DIR.glob("*/*.jsonl"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if "chat_id=" not in line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            content = (obj.get("message") or {}).get("content")
            if not isinstance(content, str):
                continue
            m = pat.search(content)
            if not m:
                continue
            ts = obj.get("timestamp", "")
            if ts > best_ts:
                best_ts, best_id = ts, m.group(1)
    return best_id


def post(channel: str, text: str) -> bool:
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{channel}/messages",
        data=json.dumps({"content": text}).encode("utf-8"),
        headers={
            "Authorization": f"Bot {token()}",
            "Content-Type": "application/json",
            "User-Agent": "shirabe-matome/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return 200 <= r.status < 300
    except urllib.error.HTTPError as e:
        print(f"エラー: Discord API {e.code}: {e.read().decode('utf-8', 'replace')[:300]}", file=sys.stderr)
    except urllib.error.URLError as e:
        print(f"エラー: Discord 接続失敗: {e.reason}", file=sys.stderr)
    return False


def build_message(date: str, topics: list[dict]) -> str:
    if not topics:
        return f"📓 **{date}** — 記事にする価値のあるトピックはありませんでした。"

    lines = [f"📓 **{date}** のドラフト {len(topics)} 件", ""]
    for i, t in enumerate(topics, 1):
        flag = " ⚠️" if t.get("needs_review") else ""
        lines.append(f"**{i}. {t.get('title', '(無題)')}**{flag}")
        tags = " / ".join(t.get("tags") or [])
        if tags:
            lines.append(f"　`{tags}`")
        if t.get("one_line"):
            lines.append(f"　{t['one_line']}")
        if t.get("needs_review") and t.get("review_note"):
            lines.append(f"　⚠️ {t['review_note']}")
        lines.append("")
    lines.append("公開するものを番号で返信してください（例: `1,3` / `all` / `none`）")

    msg = "\n".join(lines)
    if len(msg) > MAX_MSG:
        msg = msg[:MAX_MSG].rstrip() + "\n…（省略）"
    return msg


def main() -> int:
    ap = argparse.ArgumentParser(description="ドラフト一覧を Discord に通知する")
    ap.add_argument("--date", default=datetime.now(JST).strftime("%Y-%m-%d"))
    ap.add_argument("--channel", help="通知先チャンネル ID")
    ap.add_argument("--dry-run", action="store_true", help="送らずに本文だけ出す")
    args = ap.parse_args()

    index_path = DRAFTS_DIR / args.date / "index.json"
    if not index_path.is_file():
        sys.exit(f"エラー: {index_path.relative_to(ROOT)} がありません")
    topics = (json.loads(index_path.read_text(encoding="utf-8")) or {}).get("topics") or []

    msg = build_message(args.date, topics)

    if args.dry_run:
        print(msg)
        return 0

    channel = args.channel or os.environ.get("SHIRABE_DISCORD_CHANNEL") or newest_channel()
    if not channel:
        sys.exit("エラー: 通知先チャンネルが決まりません（--channel で指定してください）")

    if not post(channel, msg):
        return 1
    print(f"✓ Discord に通知しました（{len(topics)} 件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
