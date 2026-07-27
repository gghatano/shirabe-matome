# 調べたことまとめ

その日に調べたこと・話したことをトピックごとにまとめ、カレンダーと個別カードから
それぞれのページへ辿れるようにした静的サイト。

見た目とインタラクションは [suginami-matome](https://github.com/gghatano/suginami-matome) を踏襲している。

## 構成

```
shirabe-matome/
├── index.html            一覧ページ（カレンダー + カード）
├── assets/base.css       一覧と個別ページで共有するスタイル
├── templates/entry.html  個別ページのテンプレート
├── build.py              posts/*.md → entries/*.html + data/entries.json
├── posts/                公開すると決めた Markdown ← 唯一のソース
├── drafts/               未承認のドラフト（gitignore）
├── entries/              生成物（gitignore）
└── data/entries.json     生成物（gitignore）
```

`posts/` だけが人の手で管理する場所。`entries/` と `data/` はいつ消してもビルドで戻る。

## ビルド

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python build.py            # 差分ビルド
.venv/bin/python build.py --clean    # entries/ を消してから作り直す
```

ローカルで確認するときは `fetch()` を使っている都合上、`file://` ではなく HTTP で開く。

```bash
python -m http.server 8000
# → http://localhost:8000/
```

## 記事の書き方

`posts/YYYY-MM-DD-slug.md` に置く。先頭に YAML フロントマターが必要。

```markdown
---
title: 会話ログを毎日まとめて公開する仕組みの設計
date: 2026-07-27
icon: 📰
tags: [設計, 自動化]
summary: 一覧カードに出る2〜3行の要約。省略すると本文の先頭段落から作られる。
lead: 個別ページの見出し下に出る1行。省略可。
sources:
  - label: 参照したページ
    url: https://example.com/
---

本文（Markdown）
```

| キー | 必須 | 内容 |
|---|---|---|
| `title` | ○ | 記事タイトル |
| `date` | △ | `YYYY-MM-DD`。省略時はファイル名から取る |
| `tags` | | 一覧のタグ絞り込みに使う。色は自動割り当て |
| `icon` | | カードのサムネイルに出す絵文字 |
| `summary` | | 一覧カードの要約。省略時は本文から自動生成 |
| `lead` | | 個別ページのリード文 |
| `sources` | | 記事末尾の「参照したもの」 |

タグの色は `build.py` の `PALETTE` からタグ名順に決定的に割り当てられる。
同じタグは常に同じ色になり、記事を足しても既存の色は変わらない。

## デプロイ

`main` への push で `.github/workflows/pages.yml` が動く。CI 側で `build.py --clean` を
実行してから Pages にアップロードするので、生成物をコミットする必要はない。

Pages のソース設定は `configure-pages` の `enablement: true` が面倒を見るため、
リポジトリ作成後に Settings を手で触る必要はない。

## 公開フロー

### ネタ元

2つを混ぜる。

| 元 | 場所 | 備考 |
|---|---|---|
| Claude Code の会話ログ | `~/.claude/projects/*/**.jsonl` | プロジェクト単位でディレクトリが分かれている |
| Discord の会話 | Discord API（`fetch_messages`） | 雑談が混ざるので、まとめる価値のあるやりとりだけ拾う除外フィルタが要る |

どちらも「その日の分」を日付で拾って1本のトピック列に統合し、重複する話題はまとめる。
同じ話を両方でしていることがあるので、統合はソース単位ではなくトピック単位で行う。

### 2段階の承認

定時ジョブは人の返信を待てないので、間を `drafts/` のファイルでつなぐ。

```
フェーズ1（定時）  その日のログを要約 → drafts/YYYY-MM-DD/ に保存 → 一覧を通知
フェーズ2（返信時） 選ばれたものを posts/ へ移動 → commit → push → 自動デプロイ
```

`drafts/` は gitignore されているので、明示的に `posts/` へ移すまで公開されない。
既定が「出さない」側に倒れているのが重要で、逆にすると事故ったときに取り返しがつかない。

## 注意

`posts/` に入っている記事はレイアウト確認用のサンプル。実運用の前に差し替えるか削除する。
