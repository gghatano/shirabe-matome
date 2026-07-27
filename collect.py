#!/usr/bin/env python3
"""その日の会話素材を集めて 1 枚の Markdown にまとめる。

素材は2つ。

1. Claude Code の会話ログ  ~/.claude/projects/*/*.jsonl
2. Discord のチャンネル履歴  Discord REST API

Discord のやりとりが Claude Code 経由で行われた場合、それは 1 のログにも
`<channel ...>` 付きで残っている。二重に載らないよう message_id で重複を除く。

出力は drafts/<日付>/material.md。この 1 枚を要約段の入力にする。

使い方:
    python collect.py                     # 今日（JST）
    python collect.py --date 2026-07-26
    python collect.py --no-discord        # ログだけ
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DRAFTS_DIR = ROOT / "drafts"
PROJECTS_DIR = Path.home() / ".claude" / "projects"
DISCORD_ENV = Path.home() / ".claude" / "channels" / "discord" / ".env"

JST = timezone(timedelta(hours=9))

# 1 メッセージあたりの上限。長い出力は要約には不要なので頭だけ残す。
MAX_TEXT = 2400
# 1 日ぶんの合計上限。超えたら古いものから落とす。
MAX_TOTAL = 160_000

# 属性の並び順に依存しないよう、タグ全体を取ってから属性を辞書に開く
CHANNEL_RE = re.compile(r"<channel\s+(?P<attrs>[^>]*)>(?P<body>.*?)</channel>", re.DOTALL)
ATTR_RE = re.compile(r'(\w+)="([^"]*)"')
SYSREM_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)

# ツール入力のうち「何を調べ、何を触ったか」が分かるキー
TOOL_FIELDS = {
    "Bash": "command",
    "Read": "file_path",
    "Edit": "file_path",
    "Write": "file_path",
    "NotebookEdit": "notebook_path",
    "WebFetch": "url",
    "WebSearch": "query",
    "Glob": "pattern",
    "Grep": "pattern",
    "Task": "description",
    "Agent": "description",
    "Skill": "skill",
}
# 記録しても要約の役に立たないツール
TOOL_SKIP = {"TodoWrite", "ToolSearch", "ReportFindings"}


def clip(s: str, n: int = MAX_TEXT) -> str:
    s = s.strip()
    return s if len(s) <= n else s[:n].rstrip() + f" …（残り{len(s) - n:,}字省略）"


def to_jst(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(JST)
    except (ValueError, AttributeError):
        return None


def project_label(path: Path) -> str:
    """~/.claude/projects/-home-user-workspace-projects-foo → workspace/projects/foo"""
    name = path.parent.name.lstrip("-")
    parts = name.split("-")
    # ホームディレクトリ部分（home/<user>）は冗長なので落とす
    if len(parts) >= 2 and parts[0] == "home":
        parts = parts[2:]
    return "/".join(parts) or name


# ---------------------------------------------------------------- 会話ログ

def collect_transcripts(day: str) -> tuple[list[dict], set[str], Counter]:
    """指定日の発言を時系列で返す。あわせて Discord の message_id 集合と統計も返す。"""
    events: list[dict] = []
    seen_discord_ids: set[str] = set()
    stats = Counter()

    for path in sorted(PROJECTS_DIR.glob("*/*.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                stats["broken_line"] += 1
                continue

            dt = to_jst(o.get("timestamp", ""))
            if dt is None or dt.strftime("%Y-%m-%d") != day:
                continue
            if o.get("isSidechain"):
                stats["sidechain_skipped"] += 1
                continue

            typ = o.get("type")
            msg = o.get("message") or {}
            content = msg.get("content")
            base = {
                "at": dt,
                "session": (o.get("sessionId") or "")[:8],
                "project": project_label(path),
            }

            if typ == "user":
                # ツール結果は本文ではないので落とす（量が桁違いに多い）
                if isinstance(content, list):
                    if any(b.get("type") == "tool_result" for b in content):
                        stats["tool_result_skipped"] += 1
                        continue
                    text = "\n".join(b.get("text", "") for b in content if b.get("type") == "text")
                else:
                    text = content if isinstance(content, str) else ""

                text = SYSREM_RE.sub("", text).strip()
                if not text:
                    continue

                m = CHANNEL_RE.search(text)
                if m:
                    a = dict(ATTR_RE.findall(m.group("attrs")))
                    msg_id = a.get("message_id", "")
                    # 同じ Discord メッセージが複数セッションに配信されることがある。
                    # 先に見たほうだけ残す。
                    if msg_id and msg_id in seen_discord_ids:
                        stats["discord_dup_skipped"] += 1
                        continue
                    if msg_id:
                        seen_discord_ids.add(msg_id)
                    # 並べ替えには送信時刻を使う。セッションが記録した時刻とは
                    # 数分ずれることがあり、Discord から直接拾った分と混ぜると順序が狂う。
                    sent = to_jst(a.get("ts", "")) or dt
                    events.append({**base, "at": sent, "kind": "discord_in",
                                   "who": a.get("user", "?"), "chat": a.get("chat_id", ""),
                                   "text": clip(m.group("body"))})
                    stats["discord_in"] += 1
                else:
                    events.append({**base, "kind": "prompt", "text": clip(text)})
                    stats["prompt"] += 1

            elif typ == "assistant" and isinstance(content, list):
                tools = []
                for b in content:
                    bt = b.get("type")
                    if bt == "text" and b.get("text", "").strip():
                        events.append({**base, "kind": "reply", "text": clip(b["text"])})
                        stats["reply"] += 1
                    elif bt == "tool_use":
                        name = b.get("name") or "?"
                        inp = b.get("input") or {}
                        # Discord への返信は本文そのもの。ツール扱いにせず発言として残す。
                        if name.endswith("__reply"):
                            events.append({**base, "kind": "discord_out",
                                           "text": clip(str(inp.get("text", "")))})
                            stats["discord_out"] += 1
                            continue
                        if name in TOOL_SKIP:
                            continue
                        field = TOOL_FIELDS.get(name.split("__")[-1]) or TOOL_FIELDS.get(name)
                        detail = str(inp.get(field, "")) if field else ""
                        tools.append(f"{name}: {clip(detail, 120)}" if detail else name)
                if tools:
                    events.append({**base, "kind": "tools", "tools": tools})
                    stats["tool_use"] += len(tools)

    events.sort(key=lambda e: e["at"])
    return events, seen_discord_ids, stats


# ---------------------------------------------------------------- Discord

def discord_token() -> str | None:
    if not DISCORD_ENV.is_file():
        return None
    for line in DISCORD_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("DISCORD_BOT_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def discord_get(path: str, token: str) -> list | dict | None:
    req = urllib.request.Request(
        f"https://discord.com/api/v10{path}",
        headers={"Authorization": f"Bot {token}", "User-Agent": "shirabe-matome/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"  Discord API {e.code}: {path}", file=sys.stderr)
    except urllib.error.URLError as e:
        print(f"  Discord 接続失敗: {e.reason}", file=sys.stderr)
    return None


def collect_discord(day: str, channels: set[str], skip_ids: set[str]) -> list[dict]:
    """ログに残っていない Discord の発言を拾う（Claude を経由しなかったもの）。"""
    token = discord_token()
    if not token:
        print("  Discord トークンが無いのでスキップ", file=sys.stderr)
        return []

    # 自分（bot）の発言はログ側に discord_out として既にある。二重に載せない。
    me = discord_get("/users/@me", token)
    my_id = (me or {}).get("id") if isinstance(me, dict) else None

    events = []
    for chat_id in sorted(channels):
        data = discord_get(f"/channels/{chat_id}/messages?limit=100", token)
        if not isinstance(data, list):
            continue
        for m in data:
            if m.get("id") in skip_ids:
                continue
            if my_id and (m.get("author") or {}).get("id") == my_id:
                continue
            dt = to_jst(m.get("timestamp", ""))
            if dt is None or dt.strftime("%Y-%m-%d") != day:
                continue
            body = (m.get("content") or "").strip()
            if not body:
                continue
            author = m.get("author") or {}
            events.append({
                "at": dt,
                "session": "-",
                "project": f"discord/{chat_id}",
                "kind": "discord_bot" if author.get("bot") else "discord_in",
                "who": author.get("username") or "?",
                "chat": chat_id,
                "text": clip(body),
            })
    return events


# ---------------------------------------------------------------- 出力

LABEL = {
    "prompt": "👤 依頼",
    "reply": "🤖 応答",
    "discord_in": "💬 Discord",
    "discord_out": "🤖 Discord返信",
    "discord_bot": "🤖 Discord(bot)",
    "tools": "🔧 操作",
}


def render(day: str, events: list[dict], stats: Counter) -> str:
    out = [
        f"# {day} の会話素材",
        "",
        "この文書は自動生成された要約の入力。人が読むためのものではない。",
        "",
        "## 収集の内訳",
        "",
        "| 種別 | 件数 |",
        "|---|---|",
    ]
    for k, v in stats.most_common():
        out.append(f"| {k} | {v} |")
    out += ["", "---", ""]

    last_key = None
    for e in events:
        key = (e["project"], e["session"])
        if key != last_key:
            out += ["", f"## {e['project']}（session {e['session']}）", ""]
            last_key = key
        t = e["at"].strftime("%H:%M")
        if e["kind"] == "tools":
            out.append(f"**{t} {LABEL['tools']}** — " + " / ".join(f"`{x}`" for x in e["tools"]))
        else:
            who = f" @{e['who']}" if e.get("who") else ""
            out += [f"**{t} {LABEL.get(e['kind'], e['kind'])}{who}**", "", e["text"], ""]
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="その日の会話素材を集める")
    ap.add_argument("--date", default=datetime.now(JST).strftime("%Y-%m-%d"),
                    help="対象日 (YYYY-MM-DD, JST)。既定は今日")
    ap.add_argument("--no-discord", action="store_true", help="Discord API を叩かない")
    ap.add_argument("--out", help="出力先。既定は drafts/<日付>/material.md")
    args = ap.parse_args()

    try:
        datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        print(f"エラー: --date は YYYY-MM-DD 形式で（現在: {args.date!r}）", file=sys.stderr)
        return 1

    if not PROJECTS_DIR.is_dir():
        print(f"エラー: 会話ログが見つかりません: {PROJECTS_DIR}", file=sys.stderr)
        return 1

    print(f"● {args.date} の素材を集めます")
    events, seen_ids, stats = collect_transcripts(args.date)
    print(f"  会話ログ: {len(events)} 件")

    if not args.no_discord:
        channels = {e["chat"] for e in events if e.get("chat")}
        if channels:
            extra = collect_discord(args.date, channels, seen_ids)
            print(f"  Discord 追加分: {len(extra)} 件（ログ済み {len(seen_ids)} 件は除外）")
            stats["discord_extra"] = len(extra)
            events = sorted(events + extra, key=lambda e: e["at"])
        else:
            print("  Discord: 対象チャンネルなし")

    if not events:
        print(f"  {args.date} の素材はありませんでした")
        return 2

    # 合計が上限を超える場合は古いものから落とす（直近ほど要約に効く）
    total = sum(len(e.get("text", "")) for e in events)
    if total > MAX_TOTAL:
        kept, acc = [], 0
        for e in reversed(events):
            acc += len(e.get("text", ""))
            if acc > MAX_TOTAL:
                break
            kept.append(e)
        dropped = len(events) - len(kept)
        events = list(reversed(kept))
        stats["truncated_old_events"] = dropped
        print(f"  上限 {MAX_TOTAL:,} 字を超えたため古い {dropped} 件を省略")

    out_path = Path(args.out) if args.out else DRAFTS_DIR / args.date / "material.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render(args.date, events, stats), encoding="utf-8")
    print(f"✓ {out_path.relative_to(ROOT) if out_path.is_relative_to(ROOT) else out_path}"
          f"（{out_path.stat().st_size:,} bytes）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
