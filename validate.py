#!/usr/bin/env python3
"""ドラフトの frontmatter を検証し、直せるものは直す。

    python validate.py --date 2026-07-27
    python validate.py --date 2026-07-27 --check   # 直さず検査だけ

要約段（LLM）が生成する frontmatter で最も多い壊れ方は、値にコロンを含むのに
クォートで囲んでいないケース。技術記事のタイトルは `align-items: flex-start` の
ように普通にコロンを含むため、放っておくと日常的に発生する。

この1パターンだけを決定的に直す。それ以外の壊れ方は直さず、報告して終わる。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
DRAFTS_DIR = ROOT / "drafts"
JST = timezone(timedelta(hours=9))

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
# インデントなしの `key: value` 行だけを対象にする（リストや入れ子には触らない）
SCALAR_LINE_RE = re.compile(r"^(?P<key>[A-Za-z_][\w-]*): (?P<val>\S.*)$")


def quote_scalar(val: str) -> str:
    return '"' + val.replace("\\", "\\\\").replace('"', '\\"') + '"'


def repair(front: str) -> tuple[str, list[str]]:
    """コロンを含む未クォートのスカラー値を囲む。直した key の一覧も返す。"""
    fixed, changed = [], []
    for line in front.splitlines():
        m = SCALAR_LINE_RE.match(line)
        if not m:
            fixed.append(line)
            continue
        val = m.group("val").strip()
        # 既にクォート済み / リストや辞書の開始 / コロンを含まない → そのまま
        if (val[0] in "\"'[{" or ": " not in val) and not val.endswith(":"):
            fixed.append(line)
            continue
        fixed.append(f"{m.group('key')}: {quote_scalar(val)}")
        changed.append(m.group("key"))
    return "\n".join(fixed), changed


def check_file(path: Path, fix: bool) -> tuple[str, str]:
    """(状態, 詳細) を返す。状態は ok / repaired / broken。"""
    text = path.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(text)
    if not m:
        return "broken", "frontmatter（--- で囲むブロック）がありません"

    try:
        yaml.safe_load(m.group(1))
        return "ok", ""
    except yaml.YAMLError as e:
        first = str(e).splitlines()[0]

    new_front, changed = repair(m.group(1))
    if not changed:
        return "broken", first
    try:
        yaml.safe_load(new_front)
    except yaml.YAMLError:
        return "broken", first

    if fix:
        path.write_text("---\n" + new_front + "\n---\n" + text[m.end():], encoding="utf-8")
    return "repaired", f"{', '.join(changed)} をクォートで囲みました"


def main() -> int:
    ap = argparse.ArgumentParser(description="ドラフトの frontmatter を検証・修復する")
    ap.add_argument("--date", default=datetime.now(JST).strftime("%Y-%m-%d"))
    ap.add_argument("--check", action="store_true", help="修復せず検査だけ")
    args = ap.parse_args()

    day_dir = DRAFTS_DIR / args.date
    if not day_dir.is_dir():
        print(f"エラー: {day_dir.relative_to(ROOT)} がありません", file=sys.stderr)
        return 1

    index_path = day_dir / "index.json"
    slugs = None
    if index_path.is_file():
        try:
            slugs = {t.get("slug") for t in json.loads(index_path.read_text(encoding="utf-8")).get("topics", [])}
        except (json.JSONDecodeError, AttributeError):
            print("警告: index.json を読めないので全 .md を対象にします", file=sys.stderr)

    targets = [p for p in sorted(day_dir.glob("*.md"))
               if p.name != "material.md" and (slugs is None or p.stem in slugs)]
    if not targets:
        print(f"{args.date}: 検証対象のドラフトはありません")
        return 0

    counts = {"ok": 0, "repaired": 0, "broken": 0}
    for p in targets:
        state, detail = check_file(p, fix=not args.check)
        counts[state] += 1
        if state == "ok":
            continue
        mark = "🔧" if state == "repaired" else "❌"
        verb = "修復" if state == "repaired" and not args.check else ("要修復" if state == "repaired" else "壊れています")
        print(f"  {mark} {p.name}: {verb} — {detail}")

    print(f"{args.date}: ok {counts['ok']} / 修復 {counts['repaired']} / 未解決 {counts['broken']}"
          f"（全 {len(targets)} 件）")
    return 1 if counts["broken"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
