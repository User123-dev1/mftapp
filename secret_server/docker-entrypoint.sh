#!/bin/sh
# Create the first administrator on first start (no-op afterwards), then serve the API.
# TLS is terminated by the reverse proxy in front of this container.
set -e
if [ -z "$SS_MASTER_KEY" ] && [ ! -f "${SS_MASTER_KEY_FILE:-secret_server.key}" ]; then
  echo "SS_MASTER_KEY is not set - refusing to start (the vault would create a key nobody has backed up)." >&2
  exit 1
fi
python -m secret_server init --admin "${SS_ADMIN_USERNAME:-admin}"
exec python -m secret_server serve --host 0.0.0.0 --port 8090 --behind-proxy
