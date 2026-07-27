#!/usr/bin/env python3
"""ドラフトを確認し、選んだものだけを公開する。

    python publish.py --date 2026-07-27              # 一覧を見るだけ（変更なし）
    python publish.py --date 2026-07-27 --select 1,3 # 1番と3番を公開
    python publish.py --date 2026-07-27 --select all
    python publish.py --date 2026-07-27 --select none

`--select` を付けない限り何も変更しない。選ばれた記事だけが drafts/ から posts/ へ移り、
ビルドとコミットが走る。選ばれなかったものは drafts/ に残るだけで公開されない。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DRAFTS_DIR = ROOT / "drafts"
POSTS_DIR = ROOT / "posts"
JST = timezone(timedelta(hours=9))

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
# index.json は LLM が生成し、その入力には第三者の文章が混ざる。slug は
# ファイルパスの組み立てに使うので、形を厳密に決めておく。
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# 承認時にだけ使うキー。公開する記事からは落とす。
REVIEW_KEYS = ("needs_review", "review_note")


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, **kw)


def load_index(date: str) -> dict:
    path = DRAFTS_DIR / date / "index.json"
    if not path.is_file():
        sys.exit(f"エラー: {path.relative_to(ROOT)} がありません。先に ./summarize.sh {date} を実行してください")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"エラー: {path.relative_to(ROOT)} が壊れています: {e}")


def show(date: str, topics: list[dict]) -> None:
    if not topics:
        print(f"{date}: ドラフトは 0 件です")
        return
    print(f"{date} のドラフト {len(topics)} 件\n")
    for i, t in enumerate(topics, 1):
        flag = "  ⚠ 要確認" if t.get("needs_review") else ""
        print(f"  {i}. {t.get('title', '(タイトルなし)')}{flag}")
        print(f"     tags: {' / '.join(t.get('tags') or []) or '-'}")
        if t.get("one_line"):
            print(f"     {t['one_line']}")
        if t.get("needs_review") and t.get("review_note"):
            print(f"     ⚠ {t['review_note']}")
        print(f"     drafts/{date}/{t.get('slug')}.md")
        print()
    print("公開する番号を選んでください:")
    print(f"  python publish.py --date {date} --select 1,3")
    print(f"  python publish.py --date {date} --select all")


def parse_select(sel: str, n: int) -> list[int]:
    sel = sel.strip().lower()
    if sel in ("none", "なし", ""):
        return []
    if sel in ("all", "全部", "すべて"):
        return list(range(n))
    # 全角数字は日本語 IME だと普通に混ざるので受け付ける。
    # str.isdigit() は '²' のような文字にも真を返し int() が落ちるため使わない。
    sel = sel.translate(str.maketrans("０１２３４５６７８９，　", "0123456789, "))
    picked = []
    for part in re.split(r"[,\s]+", sel):
        if not part:
            continue
        if not re.fullmatch(r"[0-9]+", part):
            sys.exit(f"エラー: 番号で指定してください（不正な値: {part!r}）")
        i = int(part)
        if not 1 <= i <= n:
            sys.exit(f"エラー: {i} は範囲外です（1〜{n}）")
        picked.append(i - 1)
    return sorted(set(picked))


def strip_review_keys(text: str, src: Path) -> str:
    """frontmatter から承認用のキーを落とす。"""
    m = FRONTMATTER_RE.match(text)
    if not m:
        sys.exit(f"エラー: {src.name} に frontmatter がありません")
    kept = [ln for ln in m.group(1).splitlines()
            if not any(ln.startswith(f"{k}:") for k in REVIEW_KEYS)]
    return "---\n" + "\n".join(kept) + "\n---\n" + text[m.end():]


def main() -> int:
    ap = argparse.ArgumentParser(description="ドラフトを確認して公開する")
    ap.add_argument("--date", default=datetime.now(JST).strftime("%Y-%m-%d"))
    ap.add_argument("--select", help="公開する番号（例: 1,3 / all / none）")
    ap.add_argument("--no-push", action="store_true", help="コミットまでで止める")
    ap.add_argument("--no-commit", action="store_true", help="ファイルを移すだけ")
    args = ap.parse_args()

    index = load_index(args.date)
    topics = index.get("topics") or []

    if args.select is None:
        show(args.date, topics)
        return 0

    picked = parse_select(args.select, len(topics))
    if not picked:
        print(f"{args.date}: 公開なし。ドラフトは drafts/{args.date}/ に残ります")
        return 0

    # 先に全件を検査する。1件でも駄目なら1つも公開しない。
    plan = []
    for i in picked:
        t = topics[i]
        slug = str(t.get("slug") or "")
        if not SLUG_RE.match(slug):
            sys.exit(f"エラー: {i + 1} 番の slug {slug!r} が不正です"
                     "（英小文字・数字・ハイフンのみ、先頭は英数字）")
        src = DRAFTS_DIR / args.date / f"{slug}.md"
        if not src.is_file():
            sys.exit(f"エラー: {src.relative_to(ROOT)} がありません")
        dst = POSTS_DIR / f"{args.date}-{slug}.md"
        if dst.exists():
            sys.exit(f"エラー: {dst.relative_to(ROOT)} は既にあります（重複公開）")
        plan.append((src, dst, t.get("title", slug)))

    POSTS_DIR.mkdir(exist_ok=True)
    published = []
    try:
        for src, dst, title in plan:
            dst.write_text(strip_review_keys(src.read_text(encoding="utf-8"), src), encoding="utf-8")
            published.append((dst, title))
            print(f"  + posts/{dst.name}  {title}")

        # ビルドが通るかここで確認する。通らなければ公開を巻き戻す。
        py = ROOT / ".venv" / "bin" / "python"
        r = run([str(py if py.exists() else Path(sys.executable)), "build.py"])
        if r.returncode != 0:
            raise RuntimeError(f"ビルドに失敗しました\n{(r.stderr or r.stdout).strip()}")
    except (OSError, RuntimeError, SystemExit) as e:
        # 途中で落ちた場合、書き終えたぶんを残すと未承認のまま次回コミットに混ざる
        for dst, _ in published:
            dst.unlink(missing_ok=True)
        sys.exit(f"エラー: {e}\n公開は取り消しました（posts/ は元のままです）")
    print(f"✓ ビルド成功（{len(published)} 件を追加）")

    if args.no_commit:
        return 0

    run(["git", "add", *[str(d.relative_to(ROOT)) for d, _ in published]])
    titles = "\n".join(f"- {t}" for _, t in published)
    msg = (f"{args.date} の記録を公開（{len(published)}件）\n\n{titles}\n\n"
           "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>")
    r = run(["git", "commit", "-m", msg])
    if r.returncode != 0:
        sys.exit(f"エラー: コミットに失敗しました\n{r.stdout}{r.stderr}")
    print("✓ コミットしました")

    if args.no_push:
        print("  （--no-push のため push していません）")
        return 0

    r = run(["git", "push"])
    if r.returncode != 0:
        sys.exit(f"エラー: push に失敗しました\n{r.stderr}")
    print("✓ push しました。数十秒で Pages に反映されます")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
