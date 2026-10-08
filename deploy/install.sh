#!/usr/bin/env bash
# Install or upgrade ReelVault on Ubuntu (22.04 / 24.04).
# Run from an extracted release archive:  sudo ./deploy/install.sh
set -euo pipefail

APP_DIR=/opt/reelvault
DATA_DIR=/var/lib/reelvault
CONF_DIR=/etc/reelvault
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "请使用 root 运行: sudo $0" >&2
  exit 1
fi
if [[ ! -f "$SRC_DIR/backend/pyproject.toml" ]]; then
  echo "找不到 backend/，请在解压后的发布包中运行此脚本" >&2
  exit 1
fi
if [[ ! -f "$SRC_DIR/backend/reelvault/static/index.html" ]]; then
  echo "缺少前端文件 backend/reelvault/static，请使用 Release 发布包或先执行 make build" >&2
  exit 1
fi

echo "==> 安装系统依赖 (ffmpeg)"
APT_OPTIONS=(
  -o Acquire::Retries=2
  -o Acquire::http::Timeout=30
  -o Acquire::https::Timeout=30
  -o DPkg::Lock::Timeout=120
)
apt-get "${APT_OPTIONS[@]}" update -q
DEBIAN_FRONTEND=noninteractive apt-get "${APT_OPTIONS[@]}" install -y -q ffmpeg fonts-dejavu-core fonts-noto-cjk curl ca-certificates python3

if ! command -v uv >/dev/null 2>&1; then
  echo "==> 安装 uv"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
fi

echo "==> 创建用户与目录"
id reelvault >/dev/null 2>&1 || useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin reelvault
mkdir -p "$APP_DIR" "$DATA_DIR" "$CONF_DIR"
chown reelvault:reelvault "$DATA_DIR"

echo "==> 复制程序文件到 $APP_DIR"
rm -rf "$APP_DIR/reelvault"
cp -r "$SRC_DIR/backend/reelvault" "$SRC_DIR/backend/pyproject.toml" "$SRC_DIR/backend/uv.lock" "$APP_DIR/"
[[ -f "$SRC_DIR/backend/.python-version" ]] && cp "$SRC_DIR/backend/.python-version" "$APP_DIR/"
chown -R reelvault:reelvault "$APP_DIR"

echo "==> 安装 Python 依赖"
EXTRAS=()
if [[ "${REELVAULT_TRANSCRIPTION_EXTRA:-0}" == "1" ]] || grep -Eq '^REELVAULT_TRANSCRIPTION_ENABLED=(true|1)$' "$CONF_DIR/reelvault.env" 2>/dev/null; then
  EXTRAS+=(--extra transcription)
fi
if [[ "${REELVAULT_S3_EXTRA:-0}" == "1" ]] || grep -Eq '^REELVAULT_S3__BUCKET=' "$CONF_DIR/reelvault.env" 2>/dev/null; then
  EXTRAS+=(--extra s3)
fi
sudo -u reelvault env HOME="$DATA_DIR" UV_PYTHON_INSTALL_DIR="$APP_DIR/.python" \
  UV_CACHE_DIR="$APP_DIR/.uv-cache" uv sync --project "$APP_DIR" --frozen --no-dev "${EXTRAS[@]}"

if [[ ! -f "$CONF_DIR/reelvault.env" ]]; then
  echo "==> 写入默认配置 $CONF_DIR/reelvault.env"
  cp "$SRC_DIR/deploy/reelvault.env.example" "$CONF_DIR/reelvault.env"
  chmod 640 "$CONF_DIR/reelvault.env"
  chown root:reelvault "$CONF_DIR/reelvault.env"
fi

echo "==> 配置 systemd 服务"
echo "==> 安装受限的服务配置同步辅助程序"
install -d -o root -g root -m 755 /usr/local/libexec /var/lib/reelvault-service-sync
install -d -o root -g root -m 700 /var/lib/reelvault-service-sync/private
install -d -o root -g reelvault -m 1770 /var/lib/reelvault-service-sync/requests
install -o root -g root -m 644 "$SRC_DIR/backend/reelvault/service_sync.py" /usr/local/libexec/reelvault-service-sync.py
printf '1\n' > /var/lib/reelvault-service-sync/protocol
chmod 644 /var/lib/reelvault-service-sync/protocol
if ! grep -q '^REELVAULT_SYSTEMD_SYNC=' "$CONF_DIR/reelvault.env"; then
  printf '\nREELVAULT_SYSTEMD_SYNC=true\n' >> "$CONF_DIR/reelvault.env"
fi
install -o root -g root -m 644 "$SRC_DIR/deploy/reelvault-service-sync.service" /etc/systemd/system/reelvault-service-sync.service
install -o root -g root -m 644 "$SRC_DIR/deploy/reelvault-service-sync.path" /etc/systemd/system/reelvault-service-sync.path
cp "$SRC_DIR/deploy/reelvault.service" /etc/systemd/system/reelvault.service
systemctl daemon-reload
systemctl enable --now reelvault-service-sync.path >/dev/null
systemctl enable reelvault >/dev/null
systemctl restart reelvault

PORT=$(grep -E '^REELVAULT_PORT=' "$CONF_DIR/reelvault.env" | cut -d= -f2 || true)
echo
echo "ReelVault 已启动: http://$(hostname -I | awk '{print $1}'):${PORT:-34123}"
echo "配置文件: $CONF_DIR/reelvault.env   数据目录: $DATA_DIR"
echo "查看日志: journalctl -u reelvault -f"
