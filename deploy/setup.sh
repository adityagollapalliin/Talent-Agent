#!/usr/bin/env bash
# SkillPulse server setup for Ubuntu 24.04. Run as root:   sudo bash deploy/setup.sh <git-repo-url> [branch]
# Idempotent: safe to re-run (it pulls and re-syncs instead of re-cloning).
set -euo pipefail

REPO_URL="${1:?usage: sudo bash deploy/setup.sh <git-repo-url> [branch]}"
BRANCH="${2:-main}"
APP_USER="skillpulse"
APP_DIR="/opt/skillpulse"
TZ_NAME="Asia/Kolkata"

[[ $EUID -eq 0 ]] || { echo "run as root (sudo)"; exit 1; }

echo "==> timezone: ${TZ_NAME}"
timedatectl set-timezone "${TZ_NAME}"

echo "==> packages"
apt-get update -qq
apt-get install -y -qq git curl ca-certificates sqlite3 >/dev/null

echo "==> user ${APP_USER}"
id "${APP_USER}" &>/dev/null || useradd --system --create-home --home-dir "/home/${APP_USER}" --shell /bin/bash "${APP_USER}"

echo "==> uv (for ${APP_USER})"
if [[ ! -x "/home/${APP_USER}/.local/bin/uv" ]]; then
  sudo -u "${APP_USER}" -H bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
fi
UV="/home/${APP_USER}/.local/bin/uv"

echo "==> code in ${APP_DIR}"
if [[ -d "${APP_DIR}/.git" ]]; then
  sudo -u "${APP_USER}" git -C "${APP_DIR}" fetch --quiet origin "${BRANCH}"
  sudo -u "${APP_USER}" git -C "${APP_DIR}" checkout --quiet "${BRANCH}"
  sudo -u "${APP_USER}" git -C "${APP_DIR}" pull --quiet --ff-only origin "${BRANCH}"
else
  install -d -o "${APP_USER}" -g "${APP_USER}" "${APP_DIR}"
  sudo -u "${APP_USER}" git clone --quiet --branch "${BRANCH}" "${REPO_URL}" "${APP_DIR}"
fi

echo "==> python env (uv sync)"
sudo -u "${APP_USER}" -H bash -c "cd ${APP_DIR} && ${UV} sync --no-dev --frozen"

echo "==> .env"
if [[ ! -f "${APP_DIR}/.env" ]]; then
  install -m 600 -o "${APP_USER}" -g "${APP_USER}" "${APP_DIR}/.env.example" "${APP_DIR}/.env"
  echo "    created ${APP_DIR}/.env from .env.example (chmod 600). Fill it in before the first run."
fi
chmod 600 "${APP_DIR}/.env"
install -d -o "${APP_USER}" -g "${APP_USER}" "${APP_DIR}/data" "${APP_DIR}/data/backups" "${APP_DIR}/logs" "${APP_DIR}/reports"

echo "==> systemd units"
for unit in skillpulse.service skillpulse.timer skillpulse-backup.service skillpulse-backup.timer; do
  sed -e "s#@APP_DIR@#${APP_DIR}#g" -e "s#@APP_USER@#${APP_USER}#g" -e "s#@UV@#${UV}#g" \
    "${APP_DIR}/deploy/systemd/${unit}" > "/etc/systemd/system/${unit}"
done
systemctl daemon-reload
systemctl enable --now skillpulse.timer skillpulse-backup.timer

cat <<MSG

Done. Next steps:
  1. sudo -u ${APP_USER} nano ${APP_DIR}/.env            # API keys + Zoho app password
  2. sudo -u ${APP_USER} -H bash -c 'cd ${APP_DIR} && ${UV} run skillpulse send-test'
  3. sudo -u ${APP_USER} -H bash -c 'cd ${APP_DIR} && ${UV} run skillpulse check-boards'
  4. sudo systemctl start skillpulse.service             # optional first run now
  5. systemctl list-timers 'skillpulse*'                 # next run: 06:30 ${TZ_NAME}
     journalctl -u skillpulse.service -n 100             # logs (also ${APP_DIR}/logs/)
MSG
