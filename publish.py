#!/usr/bin/env python3
"""ドラフトを確認し、選んだものだけを公開する。

日付ごとに見る:

    python publish.py --date 2026-07-27              # 一覧を見るだけ（変更なし）
    python publish.py --date 2026-07-27 --select 1,3 # 1番と3番を公開
    python publish.py --date 2026-07-27 --select all
    python publish.py --date 2026-07-27 --select none

日付をまたいで通し番号で見る（未公開分を10件ずつ）:

    python publish.py --queue                        # 1ページ目
    python publish.py --queue --page 3               # 3ページ目
    python publish.py --queue --select 12,15         # 通し番号で公開

`--select` を付けない限り何も変更しない。選ばれた記事だけが drafts/ から posts/ へ移り、
ビルドとコミットが走る。選ばれなかったものは drafts/ に残るだけで公開されない。

通し番号は drafts/_queue.json に保存され、**一度振ったら変わらない**。公開済みのものは
番号を空けたまま残す。番号がずれると、人が見た一覧と実際に公開されるものが食い違うため。
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


QUEUE_PATH = DRAFTS_DIR / "_queue.json"
PAGE_SIZE = 10


def load_queue() -> dict:
    """通し番号つきの未公開ドラフト一覧。一度振った番号は変えない。

    新しいドラフトは呼ぶたびに自動で取り込まれる。作り直す口は用意しない —
    番号を振り直すと「公開しない」の判断が消え、人が見た一覧と食い違うため。
    壊れた場合は drafts/_queue.json を手で消す（判断はやり直しになる）。
    """
    queue = {"next_n": 1, "items": []}
    if QUEUE_PATH.is_file():
        try:
            queue = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            sys.exit(f"エラー: {QUEUE_PATH.relative_to(ROOT)} が壊れています。\n"
                     "  手で消せば作り直せますが、「公開しない」の判断は失われます。")

    known = {(i["date"], i["slug"]) for i in queue["items"]}
    added = 0
    for index_path in sorted(DRAFTS_DIR.glob("*/index.json")):
        date = index_path.parent.name
        try:
            topics = json.loads(index_path.read_text(encoding="utf-8")).get("topics") or []
        except json.JSONDecodeError:
            print(f"警告: {index_path.relative_to(ROOT)} を読めません", file=sys.stderr)
            continue
        for t in topics:
            key = (date, t.get("slug"))
            if key in known:
                continue
            queue["items"].append({
                "n": queue["next_n"],
                "date": date,
                "slug": t.get("slug"),
                "title": t.get("title", ""),
                "tags": t.get("tags") or [],
                "one_line": t.get("one_line", ""),
                "needs_review": bool(t.get("needs_review")),
                "review_note": t.get("review_note", ""),
            })
            queue["next_n"] += 1
            known.add(key)
            added += 1

    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_PATH.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if added:
        print(f"（{added} 件を通し番号に追加しました）\n")
    return queue


def is_published(item: dict) -> bool:
    return (POSTS_DIR / f"{item['date']}-{item['slug']}.md").exists()


def set_declined(queue: dict, sel: str, declined: bool) -> int:
    """「公開しない」印をつける／外す。ファイルは消さないので後から戻せる。"""
    by_n = {i["n"]: i for i in queue["items"]}
    sel = sel.strip().lower()
    if sel in ("all", "全部", "すべて"):
        targets = [i for i in queue["items"] if not is_published(i)]
    else:
        sel = sel.translate(str.maketrans("０１２３４５６７８９，　", "0123456789, "))
        targets = []
        for part in re.split(r"[,\s]+", sel):
            if not part:
                continue
            if not re.fullmatch(r"[0-9]+", part):
                sys.exit(f"エラー: 番号で指定してください（不正な値: {part!r}）")
            item = by_n.get(int(part))
            if item is None:
                sys.exit(f"エラー: 通し番号 {part} は存在しません")
            targets.append(item)

    for i in targets:
        i["declined"] = declined
    QUEUE_PATH.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    verb = "公開しない" if declined else "検討対象に戻す"
    print(f"{len(targets)} 件を「{verb}」にしました（ドラフトは drafts/ に残っています）")
    if declined:
        print("  一覧には出なくなります。戻すには --undecline <番号>")
    return 0


def show_queue(queue: dict, page: int, show_declined: bool = False) -> int:
    pending = [i for i in queue["items"] if not is_published(i)
               and (show_declined or not i.get("declined"))]
    if not pending:
        n_declined = sum(1 for i in queue["items"]
                         if i.get("declined") and not is_published(i))
        print("未公開のドラフトはありません")
        if n_declined:
            print(f"（「公開しない」が {n_declined} 件あります。見るには --show-declined）")
        return 0
    pages = (len(pending) + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(1, min(page, pages))
    chunk = pending[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]

    # 今回どれを提示したかを覚えておく。次の --decline-rest がこの集合を使う。
    queue["last_offered"] = [i["n"] for i in chunk]
    QUEUE_PATH.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"未公開 {len(pending)} 件 — {page}/{pages} ページ目\n")
    for i in chunk:
        flag = "  ⚠ 要確認" if i["needs_review"] else ""
        if i.get("declined"):
            flag += "  ✕ 公開しない"
        print(f"  {i['n']:>3}. {i['title']}{flag}")
        print(f"       {i['date']}  {' / '.join(i['tags']) or '-'}")
        if i["one_line"]:
            print(f"       {i['one_line']}")
        if i["needs_review"] and i["review_note"]:
            print(f"       ⚠ {i['review_note']}")
        print()
    print(f"  公開: python publish.py --queue --select {chunk[0]['n']} --decline-rest")
    print("       （--decline-rest は、いま出した10件のうち選ばなかったものを一覧から外す）")
    if page < pages:
        print(f"  次: python publish.py --queue --page {page + 1}")
    return 0


def queue_plan(queue: dict, sel: str) -> list[tuple[str, str, str]]:
    """通し番号の指定を (日付, slug, タイトル) に解決する。"""
    by_n = {i["n"]: i for i in queue["items"]}
    sel = sel.strip().lower()
    if sel in ("none", "なし", ""):
        return []
    if sel in ("all", "全部", "すべて"):
        # 「公開しない」印のものは all に含めない。番号で明示すれば公開できる。
        chosen = [i for i in queue["items"] if not is_published(i) and not i.get("declined")]
    else:
        sel = sel.translate(str.maketrans("０１２３４５６７８９，　", "0123456789, "))
        chosen = []
        for part in re.split(r"[,\s]+", sel):
            if not part:
                continue
            if not re.fullmatch(r"[0-9]+", part):
                sys.exit(f"エラー: 番号で指定してください（不正な値: {part!r}）")
            item = by_n.get(int(part))
            if item is None:
                sys.exit(f"エラー: 通し番号 {part} は存在しません")
            if is_published(item):
                sys.exit(f"エラー: {part} 番「{item['title']}」は既に公開済みです")
            chosen.append(item)
    seen, out = set(), []
    for i in chosen:
        key = (i["date"], i["slug"])
        if key in seen:
            continue
        seen.add(key)
        out.append((i["date"], i["slug"], i["title"]))
    return out


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
    ap.add_argument("--queue", action="store_true", help="日付をまたいだ通し番号で扱う")
    ap.add_argument("--page", type=int, default=1, help="--queue のページ番号（10件ずつ）")
    ap.add_argument("--decline", help="公開しない印をつける番号（例: 1,5 / all）")
    ap.add_argument("--undecline", help="公開しない印を外す番号")
    ap.add_argument("--show-declined", action="store_true", help="公開しない印のものも一覧に出す")
    ap.add_argument("--decline-rest", action="store_true",
                    help="直前に提示した組のうち、選ばなかったものを一覧から外す")
    args = ap.parse_args()

    if args.queue:
        queue = load_queue()
        if args.decline:
            return set_declined(queue, args.decline, True)
        if args.undecline:
            return set_declined(queue, args.undecline, False)
        if args.select is None:
            return show_queue(queue, args.page, args.show_declined)

        specs = queue_plan(queue, args.select)
        rc = do_publish(specs, "選択分", args.no_commit, args.no_push)

        # 提示したうち選ばれなかったものを一覧から外す。公開が失敗したら
        # do_publish が抜けるので、ここまで来た＝選択分は確定している。
        if args.decline_rest:
            chosen = {(d, s) for d, s, _ in specs}
            rest = [i["n"] for i in queue["items"]
                    if i["n"] in set(queue.get("last_offered") or [])
                    and (i["date"], i["slug"]) not in chosen
                    and not i.get("declined")]
            if rest:
                print()
                set_declined(queue, ",".join(map(str, rest)), True)
            else:
                print("\n（外す対象はありませんでした）")
        return rc

    index = load_index(args.date)
    topics = index.get("topics") or []

    if args.select is None:
        show(args.date, topics)
        return 0

    picked = parse_select(args.select, len(topics))
    if not picked:
        print(f"{args.date}: 公開なし。ドラフトは drafts/{args.date}/ に残ります")
        return 0

    specs = [(args.date, str(topics[i].get("slug") or ""), topics[i].get("title", "")) for i in picked]
    return do_publish(specs, args.date, args.no_commit, args.no_push)


def do_publish(specs: list[tuple[str, str, str]], label: str,
               no_commit: bool, no_push: bool) -> int:
    """(日付, slug, タイトル) の並びを公開する。日付ごとでも通し番号でも同じ経路を通す。"""
    if not specs:
        print("公開なし。ドラフトはそのまま残ります")
        return 0

    # 先に全件を検査する。1件でも駄目なら1つも公開しない。
    plan = []
    for date, slug, title in specs:
        if not SLUG_RE.match(slug):
            sys.exit(f"エラー: slug {slug!r} が不正です"
                     "（英小文字・数字・ハイフンのみ、先頭は英数字）")
        src = DRAFTS_DIR / date / f"{slug}.md"
        if not src.is_file():
            sys.exit(f"エラー: {src.relative_to(ROOT)} がありません")
        dst = POSTS_DIR / f"{date}-{slug}.md"
        if dst.exists():
            sys.exit(f"エラー: {dst.relative_to(ROOT)} は既にあります（重複公開）")
        plan.append((src, dst, title or slug))

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

    if no_commit:
        return 0

    run(["git", "add", *[str(d.relative_to(ROOT)) for d, _ in published]])
    titles = "\n".join(f"- {t}" for _, t in published)
    msg = (f"{label} の記録を公開（{len(published)}件）\n\n{titles}\n\n"
           "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>")
    r = run(["git", "commit", "-m", msg])
    if r.returncode != 0:
        sys.exit(f"エラー: コミットに失敗しました\n{r.stdout}{r.stderr}")
    print("✓ コミットしました")

    if no_push:
        print("  （--no-push のため push していません）")
        return 0

    r = run(["git", "push"])
    if r.returncode != 0:
        sys.exit(f"エラー: push に失敗しました\n{r.stderr}")
    print("✓ push しました。数十秒で Pages に反映されます")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
