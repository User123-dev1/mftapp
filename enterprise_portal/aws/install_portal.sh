#!/bin/bash
# Installs the Enterprise App Portal on Amazon Linux 2023 and runs it on port 80.
# Used as EC2 user data by portal-ec2.yaml; safe to re-run to update the portal.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/User123-dev1/mftapp.git}"
BRANCH="${BRANCH:-main}"
APP_DIR=/opt/mftapp

dnf install -y git python3.11 python3.11-pip

id portal >/dev/null 2>&1 || useradd --system --no-create-home portal

if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --depth 1 origin "$BRANCH"
  git -C "$APP_DIR" reset --hard FETCH_HEAD
else
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

python3.11 -m venv /opt/portal-venv
/opt/portal-venv/bin/pip install --quiet -r "$APP_DIR/enterprise_portal/requirements.txt"

cat > /etc/systemd/system/portal.service <<UNIT
[Unit]
Description=Enterprise App Portal
After=network-online.target

[Service]
User=portal
WorkingDirectory=$APP_DIR/enterprise_portal
Environment=PORTAL_PORT=80
ExecStart=/opt/portal-venv/bin/python portal_server.py
AmbientCapabilities=CAP_NET_BIND_SERVICE
Restart=always

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable portal
systemctl restart portal
