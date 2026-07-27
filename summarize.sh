#!/usr/bin/env bash
# 収集済みの material.md を記事ドラフトに要約する。
#
#   ./summarize.sh [YYYY-MM-DD]
#
# 既定は今日（JST）。drafts/<日付>/material.md が無ければ先に collect.py を実行する。
# 書き込みは drafts/ の中だけに制限している。

set -euo pipefail

cd "$(dirname "$0")"

DATE="${1:-$(TZ=Asia/Tokyo date +%F)}"
MATERIAL="drafts/${DATE}/material.md"
PYTHON="${PYTHON:-.venv/bin/python}"

if [[ ! -f "$MATERIAL" ]]; then
  echo "● $MATERIAL が無いので収集から始めます"
  "$PYTHON" collect.py --date "$DATE" || exit $?
fi

if [[ ! -f "$MATERIAL" ]]; then
  echo "エラー: $MATERIAL を用意できませんでした" >&2
  exit 1
fi

# claude の場所を自力で解決する。cron や systemd から起動されると PATH が
# ログインシェルのものと違い、mise 配下の claude が見つからない。
CLAUDE="${CLAUDE_BIN:-$(command -v claude || true)}"
if [[ -z "$CLAUDE" ]]; then
  for candidate in "$HOME/.local/share/mise/shims/claude" "$HOME/.local/bin/claude"; do
    if [[ -x "$candidate" ]]; then CLAUDE="$candidate"; break; fi
  done
fi
if [[ -z "$CLAUDE" ]]; then
  echo "エラー: claude が見つかりません。CLAUDE_BIN=/path/to/claude を指定してください" >&2
  exit 1
fi

echo "● $DATE の素材を要約します（$(wc -c < "$MATERIAL" | tr -d ' ') bytes）"

PROMPT="$(sed "s/{{DATE}}/${DATE}/g" prompts/summarize.md)"

# 権限を絞る。ここは要約段の唯一の防壁なので、緩めると影響が大きい。
#
# 読み取りもリポジトリ内に限定する。material.md には Discord から来た第三者の
# 文章がそのまま入るため、そこに仕込まれた指示で ~/.claude/channels/discord/.env
# のような秘密ファイルを読み、記事に埋め込まれる経路が実際に成立する。
# 素の "Read" / "Grep" は無制限に読めるので、必ずパスを付ける。
#
# 書き込み制限は Edit(...) で書く。Write(drafts/**) と書いても
# ファイル権限チェックには一致せず、制限として機能しない
# （Edit ルールが Write を含む全ファイル編集ツールを覆う）。
#
# --permission-mode acceptEdits も付けない。編集を無条件に通すため、
# 上のパス制限が意味を失う。既定のモードなら許可されていない操作は拒否される。
INDEX="drafts/${DATE}/index.json"
ROOT="$(pwd)"

# 既に一覧があるなら作り直さない。
#
# 通知した一覧の番号を見て人が「1だけ公開」と答える設計なので、要約をやり直すと
# その番号が別の記事を指すようになる。承認が宙に浮き、見ていないものを公開しかねない。
# 作り直したいときは明示的に --force を付ける（＝出した番号を捨てる意思表示）。
if [[ -f "$INDEX" && "${2:-}" != "--force" ]]; then
  echo "● $DATE は既に要約済みです。作り直しません"
  echo "  一覧: $PYTHON publish.py --date $DATE"
  echo "  作り直す場合: $0 $DATE --force（通知済みの番号は無効になります）"
  exit 0
fi

# 相対・絶対どちらの形で書きにいっても通るようにしておく。
run_summarizer() {
  "$CLAUDE" -p "$PROMPT" \
    --allowedTools "Read(./**)" "Glob(./**)" "Grep(./**)" \
                   "Edit(drafts/**)" "Edit(//${ROOT}/drafts/**)"
}

# 何を書くかはモデルの判断なので、たまに「権限が要る」と報告して
# 1ファイルも書かずに終わることがある（Bash のリダイレクトを試して弾かれるなど）。
# 出力が無ければ1度だけやり直す。
run_summarizer
if [[ ! -f "$INDEX" ]]; then
  echo "⚠ $INDEX が作られなかったのでやり直します（1回だけ）" >&2
  run_summarizer
fi

if [[ ! -f "$INDEX" ]]; then
  echo "エラー: $INDEX が作られませんでした（2回試行）" >&2
  exit 1
fi

# frontmatter の壊れは LLM 出力で日常的に起きる（値のコロンをクォートし忘れる）。
# 直せるものはここで直しておく。放置すると公開時にビルドが落ちて差し戻される。
if ! "$PYTHON" validate.py --date "$DATE"; then
  echo "エラー: 直せない frontmatter があります。上の該当ファイルを手で直してください" >&2
  exit 1
fi

N="$("$PYTHON" -c "import json,sys; print(len(json.load(open(sys.argv[1]))['topics']))" "$INDEX")"
echo "✓ $DATE: ${N} 件のドラフトを $(dirname "$INDEX")/ に作成しました"
echo "  確認: $PYTHON publish.py --date $DATE"
