#!/usr/bin/env bash
# Lighthouse installer for Linux with systemd. Safe to run again: it keeps your config and tokens.
#
#   sudo ./install.sh
#
# Installs Fresnel (the relay) and the board under /opt/lighthouse, creates a service account,
# writes config.yaml by asking a few questions, and starts a systemd service. Everything lives in
# one folder (config, data and code under /opt/lighthouse/app/fresnel) unless you choose otherwise.
set -euo pipefail

INSTALL_DIR=/opt/lighthouse
SERVICE_USER=lighthouse
CONFIG_DIR=""   # default: the app folder, $INSTALL_DIR/app/fresnel
DATA_DIR=""     # default: data/ inside the app folder
REPO=https://github.com/ashenroot/Lighthouse.git
REF=main
SYSTEMD=yes
INIT_ARGS=""

usage() {
  cat <<USAGE
Usage: sudo ./install.sh [options]
  --dir DIR          install location (default $INSTALL_DIR)
  --user NAME        service account (default $SERVICE_USER)
  --config-dir DIR   where config.yaml lives (default: the app folder, DIR/app/fresnel)
  --data-dir DIR     where the relay keeps its state (default: data/ in the app folder)
  --repo URL         git repository to install from (default $REPO)
  --ref BRANCH       branch or tag (default $REF)
  --no-systemd       install and configure, but do not create or start a service
  --init-args "..."  extra arguments for 'fresnel init' (skips the questions when it includes --yes)
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dir) INSTALL_DIR=$2; shift 2 ;;
    --user) SERVICE_USER=$2; shift 2 ;;
    --config-dir) CONFIG_DIR=$2; shift 2 ;;
    --data-dir) DATA_DIR=$2; shift 2 ;;
    --repo) REPO=$2; shift 2 ;;
    --ref) REF=$2; shift 2 ;;
    --no-systemd) SYSTEMD=no; shift ;;
    --init-args) INIT_ARGS=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

say() { printf '\n==> %s\n' "$*"; }
die() { echo "install: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root: sudo ./install.sh"
command -v git >/dev/null || die "git is required (apt install git)"
command -v python3 >/dev/null || die "python3 is required (apt install python3)"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || die "Python 3.10 or newer is required"
if [ "$SYSTEMD" = yes ]; then
  command -v systemctl >/dev/null || die "systemd not found; use --no-systemd to install without a service"
fi

APP="$INSTALL_DIR/app"
FRESNEL="$APP/fresnel"
VENV="$FRESNEL/.venv"
CONFIG_DIR=${CONFIG_DIR:-$FRESNEL}
DATA_DIR=${DATA_DIR:-$FRESNEL/data}
CONFIG="$CONFIG_DIR/config.yaml"

say "Service account"
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$INSTALL_DIR" --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
  echo "created $SERVICE_USER"
else
  echo "$SERVICE_USER exists"
fi
mkdir -p "$INSTALL_DIR"

say "Code"
if [ -d "$APP/.git" ]; then
  echo "using the existing checkout at $APP (update it later with: fresnel update)"
else
  git clone --quiet --branch "$REF" "$REPO" "$APP"
fi
chown -R "$SERVICE_USER": "$INSTALL_DIR"

say "Python environment"
python3 -m venv --help >/dev/null 2>&1 || die "python3-venv is missing (apt install python3-venv)"
[ -x "$VENV/bin/python" ] || runuser -u "$SERVICE_USER" -- python3 -m venv "$VENV"
runuser -u "$SERVICE_USER" -- "$VENV/bin/python" -m pip install --quiet --upgrade pip
runuser -u "$SERVICE_USER" -- "$VENV/bin/python" -m pip install --quiet -e "$FRESNEL"
ln -sf "$VENV/bin/fresnel" /usr/local/bin/fresnel
echo "installed $(fresnel --version)"

say "Configuration"
mkdir -p "$CONFIG_DIR" "$DATA_DIR"
chown "$SERVICE_USER": "$DATA_DIR"
if [ -f "$CONFIG" ]; then
  echo "keeping your existing $CONFIG"
else
  # shellcheck disable=SC2086
  fresnel init --out "$CONFIG" --data-dir "$DATA_DIR" --owner "$SERVICE_USER" $INIT_ARGS
fi
fresnel --config "$CONFIG" check >/dev/null || die "the config did not validate; fix it and run ./install.sh again"

if [ "$SYSTEMD" = yes ]; then
  say "Service"
  cat > /etc/systemd/system/fresnel.service <<UNIT
[Unit]
Description=Fresnel relay (Lighthouse)
After=network-online.target
Wants=network-online.target

[Service]
User=$SERVICE_USER
Group=$SERVICE_USER
ExecStart=$VENV/bin/fresnel --config $CONFIG run
Restart=always
RestartSec=5
Environment=PYTHONDONTWRITEBYTECODE=1
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$DATA_DIR

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable fresnel >/dev/null 2>&1
  systemctl restart fresnel


  printf 'waiting for the relay'
  for _ in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:$PORT/v1/health" >/dev/null 2>&1; then break; fi
    printf '.'; sleep 1
  done
  echo
  curl -fsS "http://127.0.0.1:$PORT/v1/health" >/dev/null 2>&1 \
    || die "the relay did not come up. See: journalctl -u fresnel -n 30"
  echo "the relay is running on 127.0.0.1:$PORT"
fi

PORT=$(fresnel --config "$CONFIG" check --print port)
say "Done"
cat <<NEXT
Check it:            fresnel check
Dashboard:           http://127.0.0.1:${PORT:-8787}/
Publish it:          fresnel proxy-snippet      (prints an nginx block; then set server.public_url in $CONFIG)
Let a job push:      fresnel brief-step         (prints the push step for your briefing job, with a new token)
Update later:        sudo fresnel update
NEXT
