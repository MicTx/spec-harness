#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# spec server-mode installer.
#
# Deploys an exported spec runtime package as an HTTP service exposing the
# start / result / health endpoints used by async-task workflow platforms.
#
# Run from the package root or from this directory:
#   DOMAIN=spec.example.com bash server/install.sh
#
# Prereqs: domain A-record -> this host, ports 80/443 open. Debian/Ubuntu
# auto-installs Caddy; other distros see server/README.md.
set -euo pipefail

fail() { echo "ERROR: $*" >&2; exit 1; }

DOMAIN="${DOMAIN:-}"
PROJECTS_DIR="${PROJECTS_DIR:-/srv/spec-projects}"
PORT="${PORT:-8787}"
HOST="${HOST:-127.0.0.1}"

# Validate operator-supplied values before they reach privileged config files.
case "$DOMAIN" in
  ''|*$'\n'*|*$'\r'*|*$'\t'*|*' '*) fail "DOMAIN must be a hostname, e.g. DOMAIN=spec.example.com";;
esac
echo "$DOMAIN" | grep -Eq '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$' \
  || fail "DOMAIN is not a valid hostname: $DOMAIN"
case "$PORT" in
  ''|*[!0-9]*) fail "PORT must be an integer between 1 and 65535";;
esac
[ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || fail "PORT must be an integer between 1 and 65535"
case "$HOST" in
  ''|*$'\n'*|*$'\r'*|*$'\t'*|*' '*) fail "HOST must be a bind address, e.g. HOST=127.0.0.1";;
esac
echo "$HOST" | grep -Eq '^[A-Za-z0-9.:_-]+$' \
  || fail "HOST may only contain hostname/IP characters (got: $HOST)"
case "$PROJECTS_DIR" in
  /|/.) fail "PROJECTS_DIR must be a dedicated projects directory, not the filesystem root";;
  $'\n'*|*$'\n'*|*$'\r'*|*$'\t'*|*' '*|*'"'*|*"'"*|*'\'*|*..*) fail "PROJECTS_DIR must be a clean absolute path without spaces, quotes, or ..";;
esac
case "/$PROJECTS_DIR/" in
  */./*) fail "PROJECTS_DIR must not contain '.' path components";;
esac
echo "$PROJECTS_DIR" | grep -Eq '^(/[A-Za-z0-9._-]+)+/?$' \
  || fail "PROJECTS_DIR must be an absolute path (got: $PROJECTS_DIR)"

# Package root = parent of this script's dir (server/) -> holds scripts/ references/ SKILL.md.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PKG_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
[ -f "$PKG_ROOT/scripts/init_spec_package.py" ] || {
  echo "ERROR: spec scripts not found at $PKG_ROOT/scripts; run from an exported runtime package"; exit 1
}

INSTALL_DIR=/opt/spec-workflow
DEMO_DIR="$PROJECTS_DIR/demo"
SERVICE_USER=spec
ADAPTER="$INSTALL_DIR/server/server.py"

# Bearer token for /start and /result; non-loopback deployments require it.
SPEC_SERVER_TOKEN="${SPEC_SERVER_TOKEN:-}"
if [ -z "$SPEC_SERVER_TOKEN" ]; then
  if command -v openssl >/dev/null 2>&1; then
    SPEC_SERVER_TOKEN="$(openssl rand -hex 32)"
  else
    SPEC_SERVER_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  fi
fi
echo "$SPEC_SERVER_TOKEN" | grep -Eq '^[A-Za-z0-9_-]+$' || fail "SPEC_SERVER_TOKEN contains unsafe characters"

# The installer writes system directories and systemd/Caddy config; run as root.
[ "$(id -u)" -eq 0 ] || fail "run as root: sudo bash server/install.sh"
# Privilege drop for demo-repo setup only.
command -v runuser >/dev/null 2>&1 \
  || fail "runuser not found; install util-linux"
AS_SERVICE_USER=(runuser -u "$SERVICE_USER" --)

echo "==> Installing spec server-mode for https://$DOMAIN"

# 1) python3 + git + curl (git is needed by spec's branch governance, not by this script)
need_pkgs=()
command -v python3 >/dev/null || need_pkgs+=(python3)
command -v git >/dev/null || need_pkgs+=(git)
command -v curl >/dev/null || need_pkgs+=(curl)
if [ ${#need_pkgs[@]} -gt 0 ]; then
  if command -v apt-get >/dev/null; then apt-get update && apt-get install -y "${need_pkgs[@]}"
  elif command -v dnf >/dev/null; then dnf install -y "${need_pkgs[@]}"
  else echo "missing ${need_pkgs[*]}; install manually"; exit 1; fi
fi

# 2) Caddy (automatic HTTPS reverse proxy)
if ! command -v caddy >/dev/null; then
  if command -v apt-get >/dev/null; then
    apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl gnupg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
      | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    apt-get update && apt-get install -y caddy
  else
    echo "Caddy not found and no apt; install it per https://caddyserver.com/docs/install then re-run"; exit 1
  fi
fi
command -v ufw >/dev/null && ufw allow 80,443/tcp >/dev/null 2>&1 || true

# 3) Install the runtime package
mkdir -p "$INSTALL_DIR"
cp -a "$PKG_ROOT/." "$INSTALL_DIR/"
chown -R root:root "$INSTALL_DIR"

# 4) Service user
id "$SERVICE_USER" >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin "$SERVICE_USER"

# 5) Projects dir + a compliant demo repo (spec `new` needs main branch + a commit)
mkdir -p "$PROJECTS_DIR"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$PROJECTS_DIR"
"${AS_SERVICE_USER[@]}" env PROJECTS_DIR="$PROJECTS_DIR" bash -c '
  set -e
  mkdir -p "$PROJECTS_DIR/demo" && cd "$PROJECTS_DIR/demo"
  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || git init -q -b main 2>/dev/null \
    || { git init -q && git symbolic-ref HEAD refs/heads/main; }
  git config user.email "spec@uumit.local"; git config user.name "spec-server"
  [ -f README.md ] || echo "# demo project" > README.md
  git add -A; git diff --cached --quiet || git commit -qm "init demo" || true
'

# 6) systemd unit (token lives in the unit; file mode 600 keeps it root-only)
cat > /etc/systemd/system/spec-workflow.service <<UNIT
[Unit]
Description=spec server-mode adapter
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
Environment=SPEC_SKILL_DIR=$INSTALL_DIR
Environment=PORT=$PORT
Environment=HOST=$HOST
Environment=ALLOWED_ROOTS=$PROJECTS_DIR
Environment=SPEC_SERVER_TOKEN=$SPEC_SERVER_TOKEN
ExecStart=$(command -v python3) $ADAPTER
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
chmod 600 /etc/systemd/system/spec-workflow.service

# 7) Caddyfile (auto Let's Encrypt)
cat > /etc/caddy/Caddyfile <<CADDY
$DOMAIN {
    reverse_proxy 127.0.0.1:$PORT
}
CADDY
chown caddy:caddy /etc/caddy/Caddyfile 2>/dev/null || true

# 8) Start
systemctl daemon-reload
systemctl enable --now caddy
systemctl restart caddy
systemctl enable --now spec-workflow
systemctl restart spec-workflow
sleep 3

# 9) Local self-test (hard failures abort the install; -f makes HTTP>=400 fail)
health_json="$(curl -sSf --max-time 10 "http://$HOST:$PORT/v1/health")" \
  || fail "local health check failed: service did not answer healthy on $HOST:$PORT"
case "$health_json" in
  *'"status": "healthy"'*) : ;;
  *) fail "local health check failed: unexpected response: $health_json";;
esac
echo "==> local health: $health_json"

start_json="$(curl -sSf --max-time 10 -XPOST "http://$HOST:$PORT/v1/start" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $SPEC_SERVER_TOKEN" \
  -d "{\"root\":\"$DEMO_DIR\",\"goal\":\"用户登录与注册\"}")" \
  || fail "local start check failed: /start returned an HTTP error"
case "$start_json" in
  *'"slug": "'*) : ;;
  *) fail "local start check failed: no slug in response: $start_json";;
esac
echo "==> local start: $start_json"

cat <<REPORT

=========================================================
✅ spec server-mode installed. Caddy provisions HTTPS on first run (~30-60s).

Verify publicly:
  curl -s https://$DOMAIN/v1/health

Platform (UUMit) workflow form values:
  base URL : https://$DOMAIN/v1
  auth     : Bearer token (header Authorization: Bearer <token>)
  token    : $SPEC_SERVER_TOKEN
             (stored in /etc/systemd/system/spec-workflow.service, mode 600)
  start    : POST /start   params root=$DEMO_DIR, goal=用户登录与注册
  result   : GET  /result  params task_id=<from start>, root=$DEMO_DIR
  health   : GET  /health  (no auth)

Manage : systemctl status spec-workflow | journalctl -u spec-workflow -f
Update : re-run this installer (copies the latest package) + systemctl restart spec-workflow
Safety : writes only under $PROJECTS_DIR (ALLOWED_ROOTS whitelist); /start and
         /result require the Bearer token above.
=========================================================
REPORT
