#!/bin/bash
# Install or update the AbelZeta app portal on Amazon Linux 2023 (AWS) or Ubuntu 24.04 (Azure). Run as root.
# First run: called by the CloudFormation template's user data (AWS) / the GitHub deploy workflow (Azure).
# Later updates (pull new code, rebuild, restart):  sudo bash /opt/abelzeta/mftapp/deploy/install.sh
#
# Settings (environment variables; remembered in /opt/abelzeta/install.conf):
#   BRANCH        git branch of User123-dev1/mftapp to run
#   SECRET_ID     AWS Secrets Manager secret holding the generated passwords and the vault master key
#   PUBLIC_HOST   trial mode address (default: this server's public IP)
#   DOMAIN        e.g. abelzeta.com -> portal.<domain>, servicedesk.<domain>, ... with real HTTPS
#   LE_EMAIL      contact address for the Let's Encrypt certificates (domain mode)
#   PS_DEMO       Password Station demo mode (test domains, codes in Admin > Outbox): true (default) / false
#   KEY_VAULT     Azure: Key Vault holding the secrets (instead of SECRET_ID / Secrets Manager)
#   URL_PATTERN   Azure: published addresses, e.g. https://{sub}-abelzetaco.msappproxy.net (Entra
#                 Application Proxy); the server itself is then only reached on its private address
set -euo pipefail

BASE=/opt/abelzeta
CONF=$BASE/install.conf
SELF_SUM=$(sha256sum "$0" | cut -d' ' -f1)   # to notice when the update below changes this script
mkdir -p "$BASE"
# Remembered settings first; anything passed in the environment wins.
_env_branch=${BRANCH-}; _env_secret=${SECRET_ID-}; _env_host=${PUBLIC_HOST-}; _env_domain=${DOMAIN-}; _env_le=${LE_EMAIL-}
_env_psdemo=${PS_DEMO-}
_env_kv=${KEY_VAULT-}; _env_pattern=${URL_PATTERN-}
# shellcheck disable=SC1090
[ -f "$CONF" ] && . "$CONF"
BRANCH=${_env_branch:-${BRANCH:-main}}
SECRET_ID=${_env_secret:-${SECRET_ID:-}}
PUBLIC_HOST=${_env_host:-${PUBLIC_HOST:-}}
DOMAIN=${_env_domain:-${DOMAIN:-}}
LE_EMAIL=${_env_le:-${LE_EMAIL:-}}
PS_DEMO=${_env_psdemo:-${PS_DEMO:-true}}
KEY_VAULT=${_env_kv:-${KEY_VAULT:-}}
URL_PATTERN=${_env_pattern:-${URL_PATTERN:-}}
REPO_URL=${REPO_URL:-https://github.com/User123-dev1/mftapp.git}
APP_DIR=$BASE/mftapp
DATA_DIR=$BASE/data
COMPOSE_VERSION=v2.29.7

log() { echo "[abelzeta $(date -u +%H:%M:%S)] $*"; }

log "Installing packages"
if command -v apt-get >/dev/null; then          # Ubuntu (Azure)
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq >/dev/null
  apt-get install -y -qq docker.io docker-compose-v2 git python3 curl >/dev/null
else                                             # Amazon Linux (AWS)
  dnf install -y -q docker git python3 >/dev/null
fi
if command -v systemctl >/dev/null && [ -d /run/systemd/system ]; then
  systemctl enable --now docker
fi
if ! docker compose version >/dev/null 2>&1; then
  log "Installing docker compose $COMPOSE_VERSION"
  mkdir -p /usr/local/lib/docker/cli-plugins
  curl -fsSL "https://github.com/docker/compose/releases/download/$COMPOSE_VERSION/docker-compose-linux-$(uname -m)" \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
  chmod 0755 /usr/local/lib/docker/cli-plugins/docker-compose
fi

# Small servers (t3.small, 2 GB) need swap to build all the images without running out of memory.
if [ "$(awk '/MemTotal/ {print $2}' /proc/meminfo)" -lt 3500000 ] && ! swapon --show | grep -q /swapfile; then
  log "Adding a 2 GB swap file (small server)"
  [ -f /swapfile ] || { fallocate -l 2G /swapfile && chmod 0600 /swapfile && mkswap /swapfile >/dev/null; }
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap defaults 0 0' >> /etc/fstab
fi

log "Fetching branch $BRANCH"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --depth 1 origin "$BRANCH"
  git -C "$APP_DIR" reset --hard FETCH_HEAD
else
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

# If the update brought a newer version of this script, continue with that version
# (bash would otherwise keep running the old copy it has already loaded).
NEW_SCRIPT=$APP_DIR/deploy/install.sh
if [ -z "${ABELZETA_REEXEC:-}" ] && [ "$(sha256sum "$NEW_SCRIPT" | cut -d' ' -f1)" != "$SELF_SUM" ]; then
  log "The installer itself was updated - restarting with the new version"
  exec env ABELZETA_REEXEC=1 BRANCH="$BRANCH" SECRET_ID="$SECRET_ID" PUBLIC_HOST="$PUBLIC_HOST" \
    DOMAIN="$DOMAIN" LE_EMAIL="$LE_EMAIL" PS_DEMO="$PS_DEMO" KEY_VAULT="$KEY_VAULT" URL_PATTERN="$URL_PATTERN" REPO_URL="$REPO_URL" bash "$NEW_SCRIPT"
fi

if [ -n "$KEY_VAULT" ]; then
  # Azure: the proxy listens on the private address; Entra Application Proxy's connector reaches it there.
  REGION=""
  if [ -z "$PUBLIC_HOST" ]; then
    PUBLIC_HOST=$(curl -sf -m 3 --noproxy '*' -H Metadata:true \
      "http://169.254.169.254/metadata/instance/network/interface/0/ipv4/ipAddress/0/privateIpAddress?api-version=2021-02-01&format=text" || true)
    [ -n "$PUBLIC_HOST" ] || { echo "Could not read this VM's private IP; set PUBLIC_HOST" >&2; exit 1; }
  fi
else
  # Instance metadata (IMDSv2)
  TOKEN=$(curl -s -m 2 -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 300" || true)
  imds() { curl -sf -m 2 -H "X-aws-ec2-metadata-token: $TOKEN" "http://169.254.169.254/latest/meta-data/$1" || true; }
  REGION=$(imds placement/region)
  if [ -z "$DOMAIN" ] && [ -z "$PUBLIC_HOST" ]; then
    PUBLIC_HOST=$(imds public-ipv4)
    [ -n "$PUBLIC_HOST" ] || { echo "Set PUBLIC_HOST or DOMAIN" >&2; exit 1; }
  fi
fi

umask 077
SECRETS_FILE=$BASE/secrets.json
SM_FILE=$BASE/secrets.from-aws.json
KV="python3 $APP_DIR/deploy/azure/keyvault.py"
if [ -n "$KEY_VAULT" ]; then
  log "Reading secrets from Azure Key Vault $KEY_VAULT"
  $KV get "$KEY_VAULT" "$SM_FILE" || { echo "Could not read Key Vault $KEY_VAULT - nothing was changed." >&2; exit 1; }
fi
if [ -n "$SECRET_ID" ]; then
  log "Reading secrets from AWS Secrets Manager"
  # A brand-new server's permissions can take a minute or two to apply: retry before giving up.
  for attempt in $(seq 1 10); do
    if aws secretsmanager get-secret-value --region "$REGION" --secret-id "$SECRET_ID" \
         --query SecretString --output text > "$SM_FILE" 2> "$BASE/aws-error.log"; then
      break
    fi
    if [ "$attempt" = 10 ]; then
      cat "$BASE/aws-error.log" >&2
      echo "Could not read Secrets Manager secret $SECRET_ID - nothing was changed." >&2
      exit 1
    fi
    log "Secrets Manager not readable yet - retrying in 15 seconds ($attempt/10)"
    sleep 15
  done
fi
if [ -n "$SECRET_ID$KEY_VAULT" ]; then
  # Merge, never overwrite: the cloud secret store wins, but keep anything only this server has.
  python3 - "$SECRETS_FILE" "$SM_FILE" <<'PY'
import json, os, sys
local, aws = sys.argv[1], sys.argv[2]
def load(p):
    try:
        return json.loads(open(p).read() or "{}") if os.path.exists(p) else {}
    except ValueError:
        return {}
merged = {**load(local), **{k: v for k, v in load(aws).items() if v}}
open(local, "w").write(json.dumps(merged, indent=2) + "\n")
PY
else
  log "WARNING: no SECRET_ID - passwords are kept only in $SECRETS_FILE on this server"
fi

log "Generating configuration"
if [ -n "$DOMAIN" ]; then
  WHERE=(--domain "$DOMAIN"); [ -n "$LE_EMAIL" ] && WHERE+=(--email "$LE_EMAIL")
else
  WHERE=(--host "$PUBLIC_HOST")
fi
[ "$PS_DEMO" = false ] && WHERE+=(--no-ps-demo)
[ -n "$URL_PATTERN" ] && WHERE+=(--url-pattern "$URL_PATTERN" --internal-name "$(hostname -s)")
RESULT=$(python3 "$APP_DIR/deploy/configure.py" "${WHERE[@]}" --secrets "$SECRETS_FILE" --data-dir "$DATA_DIR")
python3 -c '
import json, sys
r = json.loads(sys.argv[1])
if r["generated_secrets"]:
    print("  new secrets generated: " + ", ".join(r["generated_secrets"]))
if r["recovered_secrets"]:
    print("  kept from the running apps: " + ", ".join(r["recovered_secrets"]))
' "$RESULT"
if [ -n "$SECRET_ID$KEY_VAULT" ]; then
  # Store in the cloud whenever it is missing something or differs from what the apps use.
  if ! python3 -c 'import json,sys; a=json.load(open(sys.argv[1])); b=json.loads(open(sys.argv[2]).read() or "{}"); sys.exit(0 if a==b else 1)' \
       "$SECRETS_FILE" "$SM_FILE"; then
    if [ -n "$KEY_VAULT" ]; then
      log "Saving the secrets in use to Azure Key Vault"
      $KV put "$KEY_VAULT" "$SECRETS_FILE"
    else
      log "Saving the secrets in use to AWS Secrets Manager"
      aws secretsmanager put-secret-value --region "$REGION" --secret-id "$SECRET_ID" \
        --secret-string "file://$SECRETS_FILE" >/dev/null
    fi
  fi
  rm -f "$SECRETS_FILE" "$SM_FILE" "$BASE/aws-error.log"   # the cloud store is the copy of record
fi
umask 022

cat > "$CONF" <<CONF
BRANCH=$BRANCH
SECRET_ID=$SECRET_ID
PUBLIC_HOST=$PUBLIC_HOST
DOMAIN=$DOMAIN
LE_EMAIL=$LE_EMAIL
PS_DEMO=$PS_DEMO
KEY_VAULT=$KEY_VAULT
URL_PATTERN='$URL_PATTERN'
CONF

mkdir -p "$DATA_DIR"/servicedesk "$DATA_DIR"/gmp "$DATA_DIR"/vault "$DATA_DIR"/phishguard "$DATA_DIR"/passwordstation
chown -R 10001:10001 "$DATA_DIR"     # the apps run as uid 10001 inside their containers
chmod 0750 "$DATA_DIR"

log "Building and starting containers (first build takes a few minutes)"
cd "$APP_DIR/deploy"
docker compose up -d --build --remove-orphans
# The Caddyfile is regenerated on every run; restart the proxy so it is actually loaded.
docker compose restart proxy >/dev/null
docker image prune -f >/dev/null

# Check that each address completes an HTTPS handshake from this server (no SNI, as browsers do for an IP).
CHECK_HOST=${DOMAIN:+portal.$DOMAIN}; CHECK_HOST=${CHECK_HOST:-$PUBLIC_HOST}
sleep 5
failed=0
for port in 443 8443 8444 8445 8446 8447 ${URL_PATTERN:+8448}; do
  [ -n "$DOMAIN" ] && [ "$port" != 443 ] && continue   # domain mode: one port, one name per app
  code=$(curl -sk --noproxy '*' -o /dev/null -w '%{http_code}' --resolve "$CHECK_HOST:$port:127.0.0.1" \
         "https://$CHECK_HOST:$port/" || true)
  if [ "$code" = "000" ]; then log "WARNING: HTTPS on port $port is not answering correctly"; failed=1; fi
done
[ "$failed" = 0 ] && log "HTTPS check passed"

log "Done. Addresses:"
python3 -c 'import json,sys; [print(f"  {k:15} {v}") for k, v in json.loads(sys.argv[1])["urls"].items()]' "$RESULT"
