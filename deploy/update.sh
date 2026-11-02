#!/usr/bin/env bash
# Pull the latest code and re-sync dependencies. Run as root: sudo bash /opt/skillpulse/deploy/update.sh [branch]
set -euo pipefail
BRANCH="${1:-main}"
APP_DIR="/opt/skillpulse"
APP_USER="skillpulse"
UV="/home/${APP_USER}/.local/bin/uv"
sudo -u "${APP_USER}" git -C "${APP_DIR}" pull --ff-only origin "${BRANCH}"
sudo -u "${APP_USER}" -H bash -c "cd ${APP_DIR} && ${UV} sync --no-dev --frozen"
for unit in skillpulse.service skillpulse.timer skillpulse-backup.service skillpulse-backup.timer; do
  sed -e "s#@APP_DIR@#${APP_DIR}#g" -e "s#@APP_USER@#${APP_USER}#g" -e "s#@UV@#${UV}#g" \
    "${APP_DIR}/deploy/systemd/${unit}" > "/etc/systemd/system/${unit}"
done
systemctl daemon-reload
echo "updated; next run: $(systemctl list-timers skillpulse.timer --no-pager | sed -n 2p)"
