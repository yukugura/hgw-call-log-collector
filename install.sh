#!/usr/bin/env bash
# Ubuntu用インストーラー: 仮想環境、設定ファイル、systemd timerを構成する。
set -euo pipefail

SERVICE_NAME="hgw-call-logger"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$EUID" -eq 0 ]]; then
  # root専用のLXCコンテナでは、サービスもrootとして実行する。
  INSTALL_USER="root"
  INSTALL_GROUP="root"
else
  INSTALL_USER="${SUDO_USER:-$USER}"
  INSTALL_GROUP="$(id -gn "$INSTALL_USER")"
fi

run_root() {
  if [[ "$EUID" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "必要なコマンドがありません: $1" >&2
    exit 1
  }
}

require_command systemctl
if [[ "$EUID" -ne 0 ]]; then
  require_command sudo
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python仮想環境をインストールします…"
  run_root apt-get update
  run_root apt-get install -y python3 python3-venv
fi

if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
  echo "HGWとMySQLの接続情報を入力してください。Enterで [ ] 内の既定値を使います。"

  read -r -p "HGW IP [192.168.0.1]: " HGW_IP
  HGW_IP="${HGW_IP:-192.168.0.1}"
  read -r -p "HGW Basic認証ユーザー [user]: " HGW_USER
  HGW_USER="${HGW_USER:-user}"
  read -r -s -p "HGW Basic認証パスワード: " HGW_PASSWORD
  echo
  [[ -n "$HGW_PASSWORD" ]] || { echo "HGWパスワードは必須です。" >&2; exit 1; }

  read -r -p "MySQLホスト [192.168.0.2]: " MYSQL_HOST
  MYSQL_HOST="${MYSQL_HOST:-192.168.0.2}"
  read -r -p "MySQLポート [3306]: " MYSQL_PORT
  MYSQL_PORT="${MYSQL_PORT:-3306}"
  read -r -p "MySQLデータベース [hgw_call_logs]: " MYSQL_DATABASE
  MYSQL_DATABASE="${MYSQL_DATABASE:-hgw_call_logs}"
  read -r -p "MySQLユーザー [hgwlog]: " MYSQL_USER
  MYSQL_USER="${MYSQL_USER:-hgwlog}"
  read -r -s -p "MySQLパスワード: " MYSQL_PASSWORD
  echo
  [[ -n "$MYSQL_PASSWORD" ]] || { echo "MySQLパスワードは必須です。" >&2; exit 1; }

  umask 077
  printf '%s\n' \
    "HGW_IP=$HGW_IP" \
    "HGW_SCHEME=http" \
    "HGW_BASIC_AUTH_USER=$HGW_USER" \
    "HGW_BASIC_AUTH_PASSWORD=$HGW_PASSWORD" \
    "HGW_CALL_LOG_URL=/cgi-bin/mainte.cgi?st_clog" \
    "HGW_TIMEOUT_SECONDS=10" \
    "MYSQL_HOST=$MYSQL_HOST" \
    "MYSQL_PORT=$MYSQL_PORT" \
    "MYSQL_DATABASE=$MYSQL_DATABASE" \
    "MYSQL_USER=$MYSQL_USER" \
    "MYSQL_PASSWORD=$MYSQL_PASSWORD" \
    "LOG_PHONE_NUMBERS=false" > "$SCRIPT_DIR/.env"
  echo ".env を作成しました。"
else
  echo "既存の .env を使用します。"
fi

if [[ ! -x "$SCRIPT_DIR/.venv/bin/python" || ! -x "$SCRIPT_DIR/.venv/bin/pip" ]]; then
  # 失敗したvenvの残骸だけを削除して作り直す。
  rm -rf "$SCRIPT_DIR/.venv"
  if ! python3 -m venv "$SCRIPT_DIR/.venv"; then
    echo "python3-venv をインストールして仮想環境を再作成します…"
    run_root apt-get update
    run_root apt-get install -y python3-venv
    rm -rf "$SCRIPT_DIR/.venv"
    python3 -m venv "$SCRIPT_DIR/.venv"
  fi
fi
"$SCRIPT_DIR/.venv/bin/pip" install --disable-pip-version-check --upgrade -r "$SCRIPT_DIR/requirements.txt"

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
TIMER_FILE="/etc/systemd/system/${SERVICE_NAME}.timer"

run_root tee "$SERVICE_FILE" >/dev/null <<EOF
[Unit]
Description=Collect HGW call logs into MySQL
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$INSTALL_USER
Group=$INSTALL_GROUP
WorkingDirectory=$SCRIPT_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$SCRIPT_DIR/.venv/bin/python $SCRIPT_DIR/hgw_call_logger.py
EOF

run_root tee "$TIMER_FILE" >/dev/null <<EOF
[Unit]
Description=Run HGW call-log collector every 10 seconds

[Timer]
OnBootSec=15s
OnUnitInactiveSec=10s
AccuracySec=1s
Persistent=true

[Install]
WantedBy=timers.target
EOF

run_root systemctl daemon-reload
run_root systemctl enable --now "${SERVICE_NAME}.timer"

echo
echo "インストール完了。初回取得を実行します…"
run_root systemctl start "${SERVICE_NAME}.service"
run_root systemctl --no-pager --full status "${SERVICE_NAME}.service"
echo
echo "定期実行の状態:"
run_root systemctl --no-pager --full status "${SERVICE_NAME}.timer"
echo
echo "ログ確認: journalctl -u ${SERVICE_NAME}.service -f"
