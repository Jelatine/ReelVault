#!/usr/bin/env bash
# Run ReelVault as a launchd agent (starts at login, restarts on crash).
#   ./deploy/macos/install-launchd.sh [DATA_DIR]      install / update
#   ./deploy/macos/install-launchd.sh --uninstall     remove
#   ./deploy/macos/install-launchd.sh --print [DIR]   only print the generated plist
set -euo pipefail

LABEL=com.reelvault.server
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if [[ "${1:-}" == "--uninstall" ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$TARGET"
  echo "已卸载 $LABEL"
  exit 0
fi

PRINT=0
if [[ "${1:-}" == "--print" ]]; then PRINT=1; shift; fi
DATA="${1:-$HOME/ReelVault}"

UV="$(command -v uv || true)"
FFMPEG="$(command -v ffmpeg || true)"
if [[ -z "$UV" || -z "$FFMPEG" ]]; then
  echo "需要先安装依赖: brew install uv ffmpeg" >&2
  exit 1
fi
if [[ ! -f "$REPO/backend/reelvault/static/index.html" ]]; then
  echo "尚未构建前端，请先运行: make install && make build" >&2
  exit 1
fi

PLIST=$(sed \
  -e "s|__REPO__|$REPO|g" \
  -e "s|__DATA__|$DATA|g" \
  -e "s|__UV__|$UV|g" \
  -e "s|__PATH__|$(dirname "$FFMPEG"):/usr/bin:/bin:/usr/sbin:/sbin|g" \
  "$REPO/deploy/macos/$LABEL.plist")

if [[ $PRINT == 1 ]]; then
  echo "$PLIST"
  exit 0
fi

mkdir -p "$DATA" "$HOME/Library/LaunchAgents"
echo "$PLIST" > "$TARGET"
plutil -lint "$TARGET" >/dev/null
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"
echo "ReelVault 已在后台运行: http://localhost:34123"
echo "数据目录: $DATA   日志: $DATA/reelvault.log"
