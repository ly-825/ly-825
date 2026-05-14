#!/usr/bin/env bash
set -euo pipefail

PORT="${CDP_PORT:-18800}"
PROFILE_DIR="${AMAZON_REVIEW_PIPELINE_CHROME_PROFILE:-$HOME/.amazon-review-pipeline/chrome-profile}"
LOG_FILE="${AMAZON_REVIEW_PIPELINE_CHROME_LOG:-/tmp/amazon_review_pipeline_chrome.log}"
CHROME_APP="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

mkdir -p "$PROFILE_DIR"

if ! command -v lsof >/dev/null 2>&1; then
  echo "⚠️ 未找到 lsof，跳过端口占用检查"
elif lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "✅ Chrome CDP 端口已在监听: $PORT"
  echo "   如需重启，请先关闭对应 Chrome 进程。"
  exit 0
fi

if [ ! -x "$CHROME_APP" ]; then
  echo "❌ 未找到 Chrome: $CHROME_APP"
  exit 1
fi

nohup "$CHROME_APP" \
  --remote-debugging-port="$PORT" \
  --user-data-dir="$PROFILE_DIR" \
  --no-first-run \
  --no-default-browser-check \
  > "$LOG_FILE" 2>&1 &

sleep 2

echo "✅ Chrome CDP 已启动"
echo "   端口: $PORT"
echo "   Profile: $PROFILE_DIR"
echo "   日志: $LOG_FILE"
echo ""
echo "下一步：在打开的 Chrome 中手动登录需要使用的 Amazon 站点。"
echo "例如：https://www.amazon.co.jp / https://www.amazon.com / https://www.amazon.de"
