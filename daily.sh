#!/usr/bin/env bash
# 日次ジョブ（フェーズ1）。収集 → 要約 → Discord へ通知して終わる。
#
#   ./daily.sh [YYYY-MM-DD]
#   ./daily.sh --yesterday      # 前日分（朝に走らせる定時ジョブ用）
#
# ここでは公開しない。公開は人が番号を選んだあと publish.py が行う（フェーズ2）。
# 定時ジョブは人の返信を待てないので、間を drafts/<日付>/ のファイルでつなぐ。

set -euo pipefail

cd "$(dirname "$0")"

# 朝に走らせる場合、当日を対象にすると素材がほぼ空になる。前日を指定する。
case "${1:-}" in
  --yesterday) DATE="$(TZ=Asia/Tokyo date -d yesterday +%F)" ;;
  "")          DATE="$(TZ=Asia/Tokyo date +%F)" ;;
  *)           DATE="$1" ;;
esac
PYTHON="${PYTHON:-.venv/bin/python}"

echo "=== $DATE の日次まとめ ==="

# 素材が1件も無い日は静かに終わる（通知もしない）
if ! "$PYTHON" collect.py --date "$DATE"; then
  code=$?
  if [[ $code -eq 2 ]]; then
    echo "素材が無いので何もしません"
    exit 0
  fi
  exit $code
fi

./summarize.sh "$DATE"

"$PYTHON" notify.py --date "$DATE"

echo "=== 完了。返信を待って publish.py で公開します ==="
