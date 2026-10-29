#!/usr/bin/env bash
# Publish the current clean, committed checkout without changing production data or secrets.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -n $(git status --porcelain) ]]; then echo 'Commit or stash changes before deploying.' >&2; exit 1; fi
remote=${CRM_SSH_TARGET:-root@165.227.150.59}
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
npm ci --prefix frontend
npm run build --prefix frontend
git archive HEAD | tar -xf - -C "$stage"
mkdir -p "$stage/frontend/dist"
cp -R frontend/dist/. "$stage/frontend/dist/"
tar -czf "$stage/app.tar.gz" -C "$stage" --exclude=app.tar.gz .
scp -O "$stage/app.tar.gz" "$remote:/tmp/cerato-crm-update.tar.gz"
ssh "$remote" 'set -eu; test -f /etc/cerato-crm.env; test -d /opt/cerato-crm/app; mkdir -p /opt/cerato-crm/next; tar -xzf /tmp/cerato-crm-update.tar.gz -C /opt/cerato-crm/next; /opt/cerato-crm/venv/bin/pip install -q -r /opt/cerato-crm/next/requirements.txt; chown -R cerato-crm:cerato-crm /opt/cerato-crm/next; systemctl stop cerato-crm; mv /opt/cerato-crm/app /opt/cerato-crm/previous; mv /opt/cerato-crm/next /opt/cerato-crm/app; if systemctl start cerato-crm && systemctl is-active --quiet cerato-crm; then rm -rf /opt/cerato-crm/previous /tmp/cerato-crm-update.tar.gz; else mv /opt/cerato-crm/app /opt/cerato-crm/failed; mv /opt/cerato-crm/previous /opt/cerato-crm/app; systemctl start cerato-crm; exit 1; fi'
echo 'Update installed. Production database and credentials were preserved.'
