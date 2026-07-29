# 調べたことまとめ

その日に調べたこと・話したことをトピックごとにまとめ、カレンダーと個別カードから
それぞれのページへ辿れるようにした静的サイト。

見た目とインタラクションは [suginami-matome](https://github.com/gghatano/suginami-matome) を踏襲している。

## 構成

```
shirabe-matome/
├── daily.sh              日次ジョブ: 収集 → 要約 → 通知
│   ├── collect.py        会話ログ + Discord → drafts/<日付>/material.md
│   ├── summarize.sh      material.md → drafts/<日付>/*.md（claude -p）
│   │   └── prompts/summarize.md   要約の指示
│   └── notify.py         ドラフト一覧を Discord へ
├── publish.py            選ばれたものを posts/ へ → ビルド → commit → push
│
├── index.html            一覧ページ（カレンダー + カード）
├── assets/base.css       一覧と個別ページで共有するスタイル
├── templates/entry.html  個別ページのテンプレート
├── build.py              posts/*.md → entries/*.html + data/entries.json
│
├── posts/                公開すると決めた Markdown ← 唯一のソース
├── drafts/               未承認のドラフト（gitignore）
├── entries/              生成物（gitignore）
└── data/entries.json     生成物（gitignore）
```

`posts/` だけが人の手で管理する場所。`entries/` と `data/` はいつ消してもビルドで戻る。
`drafts/` は gitignore されているので、`publish.py` で明示的に選ぶまで公開されない。

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

`enablement: true` は Pages のソース設定を切り替えてくれるが、**リポジトリ作成直後の
初回だけは効かない**。ワークフローの `GITHUB_TOKEN` には Pages サイトを新規作成する
権限が無く、`Resource not accessible by integration` で落ちる。一度だけ手で有効化する。

```bash
gh api -X POST repos/<owner>/<repo>/pages -f build_type=workflow
```

2回目以降は既にサイトが存在するので、そのまま通る。

## 公開フロー

### ネタ元

4つを混ぜる。

| 元 | 取得方法 | 備考 |
|---|---|---|
| Claude Code の会話ログ（WSL） | `~/.claude/projects/*/*.jsonl` | プロジェクト単位でディレクトリが分かれている |
| Claude Code の会話ログ（Windows） | `/mnt/c/Users/*/.claude/projects/*/*.jsonl` | WSL とは別に溜まる。読まないと半分以上落ちる |
| Discord の会話 | Discord REST API | Claude を経由しなかった発言を拾うため |
| `inbox/` に置いたファイル | ディレクトリを読む | ChatGPT など他ツールでの調べもの、記事のメモ |

`inbox/` は「この環境を通らなかった調べもの」の投入口。ファイル名の先頭に `YYYY-MM-DD` を
付ければその日の素材になり、無ければ更新時刻で判定する。

```
inbox/2026-07-28-chatgpt-http2-priority.md
```

ここに置いたものは**検証されていない**。要約段には「`📥 外部メモ` は未検証として扱い、
断定形で書かない」と指示してある。この環境で実際に動かしていない以上、他と同じ扱いに
してはいけない。

#### スキルで放り込む

`skills/inbox-note/` に Claude Code のスキルがある。調査結果を貼って「これを取り込んで」と
言えば、日付つきのファイル名・出典・検証状況を整えて `inbox/` に保存する。URL だけ渡せば
取得して要点をまとめる。

インストールは symlink を張るだけ。リポジトリ側を直せばそのまま反映される。

```bash
mkdir -p ~/.claude/skills
ln -sfn "$PWD/skills/inbox-note" ~/.claude/skills/inbox-note
```

Discord からでも使える（常駐セッションがスキルを持っているため）。外出先で読んだ記事を
そのまま投げておけば、翌朝の要約に入る。

Discord のやりとりが Claude Code 経由で行われた場合、それはログ側にも `<channel ...>` 付きで
残る。`collect.py` は message_id で重複を除くので、二重には載らない。

統合は**ソース単位ではなくトピック単位**で行う。同じ話を Claude Code と Discord の両方で
していることがあるため。

#### Windows 側のログを読む理由

同じマシンでも、WSL で動かした Claude Code と Windows で動かした Claude Code では
トランスクリプトの置き場が違う。`~/.claude` しか見ないと、Windows 側の作業が丸ごと
落ちる。実際にこれで **07-08〜07-24 の17日分がまるごと欠落**し、07-29 は日次ジョブが
「0件」を出したが、同じ日に Windows 側では 4.6 万字ぶんの作業（R のインストールなど）
をしていた。「素材が無い日」と「見ていない場所がある」は区別が付かないので、
落ちていることに気づけない。

`/mnt/c/Users/*/.claude/projects` を追加で読む。WSL からは普通のディレクトリとして
見えるので、特別な連携は要らない。`--no-windows` で切れる。
material.md では `win: <プロジェクト>` と出所が分かるようにしてある。

### 2段階の承認

定時ジョブは人の返信を待てない。セッションは処理が終われば落ちるので、返信を待ち続ける
一本のジョブは作れない。間を `drafts/<日付>/` のファイルでつなぐ。

```
フェーズ1（定時）   ./daily.sh
                    収集 → 要約 → drafts/<日付>/ に保存 → Discord へ一覧を通知 → 終了
                    ↓（ここでセッションは切れる。返信は何時間後でもいい）
フェーズ2（返信時）  python publish.py --date <日付> --select 1,3
                    選ばれた分を posts/ へ → ビルド検証 → commit → push → 自動デプロイ
```

`drafts/` は gitignore されているので、`publish.py` で明示的に選ぶまで公開されない。
既定が「出さない」側に倒れているのが重要で、逆にすると事故ったときに取り返しがつかない。
GitHub Pages は消してもキャッシュと検索インデックスが残る。

### 使い方

```bash
./daily.sh                                   # 今日ぶんを収集・要約・通知
./daily.sh --yesterday                       # 前日ぶん（定時ジョブと同じ）
./daily.sh 2026-07-26                        # 日付を指定

python publish.py --date 2026-07-26          # ドラフト一覧を見る（変更なし）
python publish.py --date 2026-07-26 --select 1,3
```

日付をまたいで通し番号で扱うこともできる。溜まったドラフトを10件ずつ見るとき用。

```bash
python publish.py --queue                    # 1ページ目（10件）
python publish.py --queue --page 3
python publish.py --queue --select 12,15     # 通し番号で公開

python publish.py --queue --decline all      # 「公開しない」印をつける
python publish.py --queue --undecline 12     # 印を外す
python publish.py --queue --show-declined    # 印つきも含めて見る
```

通し番号は `drafts/_queue.json` に保存され、**一度振ったら変わらない**。公開済みは欠番として
残る。番号がずれると、人が見た一覧と実際に公開されるものが食い違うため。

「公開しない」と決めたものは `--decline` で一覧から外す。ファイルは消さないので、後から
`--undecline` で戻せるし、印がついていても番号を明示すれば公開できる（`--select all` には
含まれない）。印をつけずに放置すると、翌日以降の新着が古いドラフトに埋もれる。

### 選ぶ作業の定型

「10件出す → 選ぶ → 選ばれなかったものは二度と出さない」を1往復で回す。

```bash
python publish.py --queue                        # ① 10件出す
python publish.py --queue --select 2 --decline-rest   # ② 選ぶ＋残りを外す
python publish.py --queue                        # ③ 次の10件（前回の残りは出ない）
```

`--decline-rest` は、**直前に `--queue` で提示した組**のうち選ばれなかったものに
「公開しない」印をつける。提示した集合は `drafts/_queue.json` の `last_offered` に
記録されるので、選択が別のコマンド実行になっても対象がずれない。

この形にしないと、見送ったドラフトが毎回先頭に居座り、新しいものが後ろに溜まり続ける。
判断済みのものを一覧から消していくのが、溜まったドラフトを捌く唯一の方法になる。

順序は「公開 → 除外」。公開が失敗すれば除外は実行されないので、公開できていないものが
黙って一覧から消えることはない。

個別に動かすこともできる。

```bash
python collect.py --date 2026-07-26 --no-discord   # 収集だけ（Discord を叩かない）
./summarize.sh 2026-07-26                          # 要約だけ
python notify.py --date 2026-07-26 --dry-run       # 通知本文の確認
```

### 脅威モデル

このパイプラインの入力には **第三者が書いた文章（Discord のメッセージ）** が混ざり、
出力は **public な GitHub Pages** に出る。しかも GitHub Pages のユーザーサイトは
全リポジトリが同一オリジン（`<user>.github.io`）なので、ここでの XSS は同じアカウントの
他のプロジェクトページにも影響する。要約段は LLM なので、入力に仕込まれた指示に
従ってしまう可能性を前提に置く。

### 安全側に倒している点

**要約段（`summarize.sh`）の権限**

- 読み取りを `Read(./**)` `Grep(./**)` でリポジトリ内に限定している。素の `Read` を
  許可すると `~/.claude/channels/discord/.env` のような秘密ファイルまで読めてしまい、
  material.md に仕込まれた指示でトークンを記事に埋め込む経路が成立する（実際に成立した）。
- 書き込みは `Edit(drafts/**)` のみ。`posts/` には触れない。パス制限は `Edit(...)` で
  書く必要がある — `Write(drafts/**)` はファイル権限チェックに一致せず、**制限として
  機能しない**。
- `--permission-mode acceptEdits` は付けない。編集を無条件に通すため、パス制限が無効化される。

**公開段（`publish.py`）**

- `--select` を付けない限り**何も変更しない**。既定は一覧表示だけ。
- `slug` を `^[a-z0-9][a-z0-9-]*$` で検証する。index.json は LLM 生成なので、
  `../../README` のような値が入るとリポジトリ外のファイルを読みにいく。
- 書き込む前に**全件を検査**する。1件でも不正なら1つも公開しない。
- 公開後に `build.py` を走らせ、失敗したら書いたファイルを全て消す。途中で落ちても
  未承認のファイルが `posts/` に残らない（残ると次回のコミットに紛れ込む）。
- 同じ日・同じ slug が既に `posts/` にあれば中断する（重複公開の防止）。

**ビルド段（`build.py`）**

- 本文の HTML を `nh3` で許可リスト方式にサニタイズする。Markdown は生 HTML を
  そのまま通すため、これが無いと `<script>` がそのまま公開ページに出る。
- `sources` の URL スキームを `http` / `https` / `mailto` に限る。frontmatter は
  サニタイザを通らないので、`javascript:` をここで弾く必要がある。
- frontmatter の値は `html.escape` で埋め込む（タイトルやタグ経由の XSS を防ぐ）。

**要約プロンプト**

- 鍵・絶対パス・未公開の案件名などを落とすよう指示している。判断がつかないものには
  `needs_review: true` が付き、通知に ⚠️ 付きで出る。

### 既知の限界

- **プロンプトインジェクション自体は防げていない。** 権限を絞ってあるので被害は
  「リポジトリ内の情報が記事に混ざる」までに限定されるが、最後の砦は人の承認。
- 端末から `<channel ...>` タグを含む文字列を打つと、収集段は Discord 発言として
  扱う。なりすませるのは自分自身だけなので実害はないが、正確ではない。
- 承認は「記事タイトルと1行説明」を見て行う。本文まで読まずに承認すると、埋め込まれた
  ものを見落とす。`drafts/<日付>/<slug>.md` を開いてから選ぶのが本来。

### 定期実行

`systemd/` の unit を入れると、毎朝 08:00 に前日分が処理される。

```bash
cp systemd/shirabe-matome.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now shirabe-matome.timer

systemctl --user list-timers shirabe-matome.timer   # 次回実行時刻
journalctl --user -u shirabe-matome.service -n 50   # 実行ログ
systemctl --user disable --now shirabe-matome.timer # 止める
```

cron ではなく systemd timer を使っている理由は2つ。

- **`Persistent=true` が効く。** PC が止まっていて実行時刻を過ぎても、次に起きたときに
  走る。cron にはこの挙動が無く、その日のぶんが丸ごと飛ぶ。
- WSL では cron デーモンが動いていないことが多い。systemd user は `Linger=yes` に
  しておけばログアウト後も動く。

時刻を変えるなら `shirabe-matome.timer` の `OnCalendar` を編集して
`systemctl --user daemon-reload` する。`OnCalendar` は**システムのローカル時刻**で
解釈されるので、TZ が UTC のマシンでは書いた時刻とずれる（`timedatectl` で確認）。

朝に走らせるので対象は前日分。service は `daily.sh --yesterday` を呼んでいる。
当日を対象にすると素材がほぼ空になるため。

**定時実行で踏みやすい罠**: cron / systemd から起動されると PATH がログインシェルと
違い、`claude` が見つからず `command not found` で落ちる。`summarize.sh` が自力で
解決するようにしてあるが、service 側でも `Environment=PATH=` を通してある。

## 注意

`posts/` に入っている記事はレイアウト確認用のサンプル。
