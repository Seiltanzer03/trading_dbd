#!/usr/bin/env bash
# Seiltanzer Terminal — установка на чистый Ubuntu/Debian сервер (root).
#
# Запуск одной командой на сервере:
#   curl -fsSL https://raw.githubusercontent.com/Seiltanzer03/trading_dbd/main/deploy/install.sh | bash
#
# Переменные окружения (необязательно):
#   PORT=8790      порт HTTP (по умолчанию 8790)
#   MODE=stream    stream (живой WS-стрим цены, по умолч.) | live (только REST) | demo
set -euo pipefail

APP_DIR=/opt/seiltanzer
REPO=https://github.com/Seiltanzer03/trading_dbd.git
PORT="${PORT:-8790}"
MODE="${MODE:-stream}"

echo "== Seiltanzer deploy: порт $PORT, режим $MODE =="

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git curl

# swap 1G — страховка при 750 МБ RAM (сборка/импорт pandas/numpy)
if ! swapon --show 2>/dev/null | grep -q .; then
  echo "== создаю swap 1G =="
  fallocate -l 1G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=1024
  chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# код
if [ -d "$APP_DIR/.git" ]; then
  echo "== обновляю репозиторий =="
  git -C "$APP_DIR" fetch origin main && git -C "$APP_DIR" reset --hard origin/main
else
  echo "== клонирую репозиторий =="
  git clone --depth 1 "$REPO" "$APP_DIR"
fi

# venv + зависимости
echo "== venv + зависимости =="
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -e "$APP_DIR"
"$APP_DIR/.venv/bin/pip" install --quiet websockets
mkdir -p "$APP_DIR/data"

# аргументы режима
ARGS="--host 0.0.0.0 --port $PORT --data-dir $APP_DIR/data"
case "$MODE" in
  stream) ARGS="$ARGS --stream" ;;
  demo)   ARGS="$ARGS --demo" ;;
  live)   : ;;
esac

# systemd-сервис (автозапуск + рестарт при падении)
echo "== systemd сервис =="
cat > /etc/systemd/system/seiltanzer.service <<EOF
[Unit]
Description=Seiltanzer Terminal
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python -m seiltanzer $ARGS
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now seiltanzer

# открыть порт в ufw, если он активен
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow "$PORT"/tcp || true
fi

sleep 3
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo "======================================================"
echo " Seiltanzer запущен.  Откройте:  http://$IP:$PORT"
echo " Статус:   systemctl status seiltanzer"
echo " Логи:     journalctl -u seiltanzer -f"
echo " Рестарт:  systemctl restart seiltanzer"
echo "======================================================"
systemctl --no-pager status seiltanzer | head -8 || true
