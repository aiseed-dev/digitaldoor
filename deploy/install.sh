#!/bin/sh
# 事業所サーバーへの導入(Debian/Ubuntu)。root で実行する。
#   sh deploy/install.sh
set -eu
APP=/opt/digitaldoor
DATA=/var/lib/digitaldoor
id digitaldoor >/dev/null 2>&1 || useradd --system --home "$DATA" --shell /usr/sbin/nologin digitaldoor
mkdir -p "$APP" "$DATA"
cp -r "$(dirname "$0")/.." "$APP/src"
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q -e "$APP/src[web]"
# 使う錠のドライバを足す(例: Sesame)。DRIVERS="digitalkey-sesame" sh deploy/install.sh
[ -n "${DRIVERS:-}" ] && "$APP/.venv/bin/pip" install -q $DRIVERS
[ -f "$DATA/site.toml" ] || "$APP/.venv/bin/digitalkey" site init "$DATA" --name "$(hostname)"
chown -R digitaldoor:digitaldoor "$DATA"
cp "$(dirname "$0")/digitaldoor-site.service" "$(dirname "$0")/digitaldoor-backup.service" "$(dirname "$0")/digitaldoor-backup.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now digitaldoor-site.service digitaldoor-backup.timer
echo "起動しました。設定: $DATA/site.toml  状態: systemctl status digitaldoor-site"
