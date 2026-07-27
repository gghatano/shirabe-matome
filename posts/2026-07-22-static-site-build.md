---
title: Markdown を静的サイトに変換する自前ビルド
date: 2026-07-22
icon: 🔨
tags: [Python, 設計]
summary: フレームワークを入れず、Python 100行台で Markdown → HTML + 一覧 JSON を作る。フロントマターの検証を早めに落とすのが安定運用のコツ。
lead: 静的サイトジェネレータを使わずに済ませたい場合の、最小限のビルドスクリプトの形。
sources:
  - label: Python-Markdown
    url: https://python-markdown.github.io/
---

## 方針

SSG（Hugo, Astro など）は強力だが、やりたいことが「Markdown を記事ページにして、一覧を JSON で出す」だけなら、学習コストと設定ファイルのほうが重くなる。

要件がこれだけなら自前で書いたほうが早い。

- YAML フロントマター + 本文
- 記事ごとに HTML を 1 枚
- 一覧・検索用に JSON を 1 つ

## フロントマターの切り出し

正規表現ひとつで足りる。

```python
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)

m = FRONTMATTER_RE.match(raw)
meta = yaml.safe_load(m.group(1)) or {}
body = raw[m.end():]
```

`re.DOTALL` で `.` を改行にマッチさせ、`*?` の非貪欲マッチで最初の `---` までを取る。貪欲マッチにすると本文中の水平線 `---` まで飲み込む。

## 検証は早く、メッセージは具体的に

ここが実運用でいちばん効く。フロントマターの不備は静かに通すと、生成物を見るまで気づけない。

- `title` が空 → その場でエラー
- `date` が `YYYY-MM-DD` でない → その場でエラー
- 同じ日付 + slug の重複 → その場でエラー

エラーメッセージには **どのファイルの何が悪いか** を必ず入れる。`date は YYYY-MM-DD 形式で書いてください（現在: '2026/07/22'）` のように現在値を出すと、直すのに元ファイルを開く必要すらなくなる。

## 日付の型に注意

YAML は `date: 2026-07-22` をクォート無しで書くと **`datetime.date` オブジェクトとして** パースする。文字列だと思って `.strip()` を呼ぶと落ちる。

```python
if isinstance(raw_date, (date, datetime)):
    iso = raw_date.strftime("%Y-%m-%d")
else:
    iso = str(raw_date).strip()
```

両方受けておけば、書き手がクォートを付けても付けなくても動く。

## 生成物と手書きを混ぜない

`posts/` が唯一のソースで、`entries/` と `data/` は完全な生成物。この境界を守ると、

- 生成物側はいつ消してもビルドで戻る
- 差分レビューでノイズになる箇所が予測できる
- 出力形式を変えたくなったら `--clean` して作り直すだけ

逆に、生成物を手で直す運用が一度でも混ざると、次のビルドで消える変更が生まれて信用できなくなる。README に「entries/ は触らない」と書くより、`--clean` を用意して実際に消せるようにしておくほうが効く。
