#!/usr/bin/env python3
"""posts/*.md から個別ページ (entries/*.html) と一覧データ (data/entries.json) を生成する。

posts/ の Markdown が唯一のソース。entries/ と data/ は生成物なので手で触らない。

使い方:
    python build.py            # 全件ビルド
    python build.py --clean    # 生成物を消してからビルド
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

import markdown
import yaml

ROOT = Path(__file__).resolve().parent
POSTS_DIR = ROOT / "posts"
ENTRIES_DIR = ROOT / "entries"
DATA_DIR = ROOT / "data"
TEMPLATE = ROOT / "templates" / "entry.html"

REPO_URL = "https://github.com/gghatano/shirabe-matome"

# タグ用パレット（白文字が乗る前提の中間〜濃いめの色）
PALETTE = [
    "#2563eb", "#0f766e", "#b45309", "#7c3aed", "#be123c",
    "#0369a1", "#4d7c0f", "#c2410c", "#6d28d9", "#0e7490",
    "#a21caf", "#15803d", "#9f1239", "#1d4ed8", "#854d0e",
]

WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]

# 日本語混じり文の目安（1分あたりの文字数）
CHARS_PER_MIN = 500

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)$")
TABLE_RE = re.compile(r"<table>.*?</table>", re.DOTALL)


class BuildError(Exception):
    pass


def parse_post(path: Path) -> dict:
    """1 件の Markdown を読み、メタデータと本文 HTML を返す。"""
    raw = path.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(raw)
    if not m:
        raise BuildError(f"{path.name}: 先頭の YAML フロントマター（--- で囲む）がありません")

    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise BuildError(f"{path.name}: フロントマターの YAML が壊れています: {e}") from e
    if not isinstance(meta, dict):
        raise BuildError(f"{path.name}: フロントマターは key: value 形式にしてください")

    body = raw[m.end():]

    stem = path.stem
    fm = FILENAME_RE.match(stem)
    slug = fm.group(2) if fm else stem

    # 日付はフロントマター優先、無ければファイル名から
    raw_date = meta.get("date") or (fm.group(1) if fm else None)
    if raw_date is None:
        raise BuildError(f"{path.name}: date が決まりません（ファイル名を YYYY-MM-DD-slug.md にするか date: を書く）")
    if isinstance(raw_date, (date, datetime)):
        iso = raw_date.strftime("%Y-%m-%d")
    else:
        iso = str(raw_date).strip()
    try:
        d = datetime.strptime(iso, "%Y-%m-%d").date()
    except ValueError as e:
        raise BuildError(f"{path.name}: date は YYYY-MM-DD 形式で書いてください（現在: {iso!r}）") from e

    title = str(meta.get("title") or "").strip()
    if not title:
        raise BuildError(f"{path.name}: title が空です")

    tags = meta.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    tags = [str(t).strip() for t in tags if str(t).strip()]

    sources = []
    for s in meta.get("sources") or []:
        if isinstance(s, str):
            sources.append({"label": s, "url": s})
        elif isinstance(s, dict) and s.get("url"):
            sources.append({"label": str(s.get("label") or s["url"]), "url": str(s["url"])})

    md = markdown.Markdown(extensions=["extra", "sane_lists", "admonition"])
    content_html = md.convert(body)
    # 幅の広い表は横スクロールさせる（モバイルで本文がはみ出さないように）
    content_html = TABLE_RE.sub(lambda m_: f'<div class="table-scroll">{m_.group(0)}</div>', content_html)

    plain = re.sub(r"<[^>]+>", "", content_html)
    plain = re.sub(r"\s+", "", plain)
    reading_min = max(1, round(len(plain) / CHARS_PER_MIN))

    summary = str(meta.get("summary") or "").strip()
    if not summary:
        # summary 未指定なら本文の最初の段落から作る
        first_p = re.search(r"<p>(.*?)</p>", content_html, re.DOTALL)
        text = re.sub(r"<[^>]+>", "", first_p.group(1)) if first_p else ""
        summary = re.sub(r"\s+", " ", text).strip()[:140]

    return {
        "id": f"{iso}-{slug}",
        "slug": slug,
        "title": title,
        "date": iso,
        "weekday": WEEKDAYS[d.weekday()],
        "tags": tags,
        "summary": summary,
        "icon": str(meta.get("icon") or "").strip(),
        "lead": str(meta.get("lead") or "").strip(),
        "sources": sources,
        "reading_min": reading_min,
        "url": f"entries/{iso}-{slug}.html",
        "_content": content_html,
        "_src": path.name,
    }


def assign_colors(entries: list[dict]) -> dict[str, str]:
    """タグ → 色。タグ名でソートして順に割り当てる（並びが安定し、差分が読みやすい）。"""
    tags = sorted({t for e in entries for t in e["tags"]})
    return {t: PALETTE[i % len(PALETTE)] for i, t in enumerate(tags)}


def fmt_full(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{int(y)}年{int(m)}月{int(d)}日"


def nav_card(entry: dict | None, direction: str) -> str:
    if entry is None:
        return '<div class="nav-card" style="visibility:hidden"></div>'
    label = "← 前の記録" if direction == "prev" else "次の記録 →"
    cls = "nav-card prev" if direction == "prev" else "nav-card next"
    return (
        f'<a class="{cls}" href="{html.escape(Path(entry["url"]).name)}">'
        f'<div class="dir">{label}</div>'
        f'<div class="t">{html.escape(entry["title"])}</div></a>'
    )


def render_entry(entry: dict, prev_e: dict | None, next_e: dict | None,
                 colors: dict[str, str], template: str) -> str:
    tag_badges = "".join(
        f'<span class="badge" style="background:{colors.get(t, "#64748b")}">{html.escape(t)}</span>'
        for t in entry["tags"]
    )
    read_badge = f'<span class="badge read">約{entry["reading_min"]}分</span>'
    lead = f'<p class="lead">{html.escape(entry["lead"])}</p>' if entry["lead"] else ""
    icon = f'{html.escape(entry["icon"])} ' if entry["icon"] else ""

    if entry["sources"]:
        items = "".join(
            f'<li><a href="{html.escape(s["url"])}" target="_blank" rel="noopener noreferrer">'
            f'{html.escape(s["label"])}</a></li>'
            for s in entry["sources"]
        )
        sources = f'<section class="sources"><h2>🔗 参照したもの</h2><ul>{items}</ul></section>'
    else:
        sources = ""

    nav = nav_card(prev_e, "prev") + nav_card(next_e, "next")

    replacements = {
        "{{TITLE}}": html.escape(entry["title"]),
        "{{DESCRIPTION}}": html.escape(entry["summary"]),
        "{{TAG_BADGES}}": tag_badges,
        "{{READ_BADGE}}": read_badge,
        "{{ICON}}": icon,
        "{{LEAD}}": lead,
        "{{DATE_FULL}}": fmt_full(entry["date"]),
        "{{WEEKDAY}}": entry["weekday"],
        "{{CONTENT}}": entry["_content"],
        "{{SOURCES}}": sources,
        "{{NAV}}": nav,
        "{{REPO_URL}}": html.escape(REPO_URL),
    }
    out = template
    for k, v in replacements.items():
        out = out.replace(k, v)
    return out


def build(clean: bool = False) -> int:
    if not POSTS_DIR.is_dir():
        raise BuildError(f"posts/ がありません: {POSTS_DIR}")
    template = TEMPLATE.read_text(encoding="utf-8")

    paths = sorted(POSTS_DIR.glob("*.md"))
    if not paths:
        raise BuildError("posts/ に .md が1件もありません")

    entries = [parse_post(p) for p in paths]

    dupes = {e["id"] for e in entries if sum(1 for x in entries if x["id"] == e["id"]) > 1}
    if dupes:
        raise BuildError(f"同じ日付+slug の記事が重複しています: {', '.join(sorted(dupes))}")

    # 新しい順（同日はタイトル順）。prev/next もこの並びに従う。
    entries.sort(key=lambda e: (e["date"], e["title"]), reverse=True)
    colors = assign_colors(entries)

    if clean:
        shutil.rmtree(ENTRIES_DIR, ignore_errors=True)
    ENTRIES_DIR.mkdir(exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)

    for i, e in enumerate(entries):
        newer = entries[i - 1] if i > 0 else None          # 一覧が新しい順なので前の要素が「次の記録」
        older = entries[i + 1] if i + 1 < len(entries) else None
        out_path = ENTRIES_DIR / Path(e["url"]).name
        out_path.write_text(render_entry(e, older, newer, colors, template), encoding="utf-8")

    payload = {
        "generated_from": "posts/*.md",
        "repo_url": REPO_URL,
        "tag_colors": colors,
        "entries": [{k: v for k, v in e.items() if not k.startswith("_")} for e in entries],
    }
    (DATA_DIR / "entries.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"✓ {len(entries)} 件をビルドしました → entries/, data/entries.json")
    for e in entries:
        print(f"  {e['date']}  {e['title']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="posts/*.md からサイトを生成する")
    ap.add_argument("--clean", action="store_true", help="entries/ を消してからビルドする")
    args = ap.parse_args()
    try:
        return build(clean=args.clean)
    except BuildError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
