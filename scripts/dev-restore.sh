#!/usr/bin/env bash
# Seed the local dev instance with a backup of the production Bambuddy.
#
#   scripts/dev-restore.sh                     # pull a fresh backup, restore locally
#   scripts/dev-restore.sh backup.zip          # restore a ZIP you already have
#   scripts/dev-restore.sh --download-only     # just fetch the ZIP
#   scripts/dev-restore.sh --yes               # no confirmation prompt
#
# The backup is restored verbatim — virtual printers, smart plugs and their
# settings come across exactly as production has them. Two Bambuddy instances
# then talk to the same printers, so decide in the local UI what to switch off
# before letting it run unattended.
#
# Environment:
#   BAMBUDDY_PROD_URL   the production instance to copy from (required; the
#                       repo is public, so no host is hardcoded here). Read from
#                       .env, same as the compose stack, or from the environment.
#   BAMBUDDY_API_KEY    API key on the production instance; needs the
#                       settings:backup permission. Also read from the
#                       bambuddy MCP server config in ~/.claude.json.
#   BAMBUDDY_DEV_URL    default http://localhost:8000
#   BAMBUDDY_DEV_KEY    only needed if auth is enabled in the local copy
#                       (it will be, once production data is restored)

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# .env is where the compose stack keeps its settings, so read the handful this
# script needs from there too rather than making the shell profile carry a
# second copy. Only known keys are taken, and only when not already exported.
if [ -f .env ]; then
    for key in BAMBUDDY_PROD_URL BAMBUDDY_API_KEY BAMBUDDY_DEV_URL BAMBUDDY_DEV_KEY; do
        eval "current=\${$key:-}"
        if [ -z "$current" ]; then
            value=$(sed -n "s/^${key}=//p" .env | head -1)
            # Strip optional surrounding quotes, the way compose does.
            value=${value%\"}; value=${value#\"}
            value=${value%\'}; value=${value#\'}
            [ -n "$value" ] && export "$key=$value"
        fi
    done
fi

PROD_URL="${BAMBUDDY_PROD_URL:-}"
DEV_URL="${BAMBUDDY_DEV_URL:-http://localhost:8000}"
OUT_DIR="$(git rev-parse --show-toplevel)/dev-data/backups"

if [ -z "$PROD_URL" ] && [ $# -eq 0 ]; then
    echo "Set BAMBUDDY_PROD_URL to the production instance, or pass a ZIP to restore." >&2
    exit 1
fi

DOWNLOAD_ONLY=false
ASSUME_YES=false
LOCAL_ZIP=""
for arg in "$@"; do
    case "$arg" in
        --download-only) DOWNLOAD_ONLY=true ;;
        -y|--yes) ASSUME_YES=true ;;
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        *) LOCAL_ZIP="$arg" ;;
    esac
done

# The key is already configured for the MCP server; reuse it rather than
# asking for a second copy in the shell profile.
if [ -z "${BAMBUDDY_API_KEY:-}" ] && [ -f "$HOME/.claude.json" ]; then
    BAMBUDDY_API_KEY="$(python3 - "$HOME/.claude.json" <<'PY' || true
import json, sys
try:
    cfg = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)

def walk(node):
    if isinstance(node, dict):
        servers = node.get("mcpServers")
        if isinstance(servers, dict):
            for name, spec in servers.items():
                if "bambuddy" in name and isinstance(spec, dict):
                    env = spec.get("env") or {}
                    for key in ("BAMBUDDY_API_KEY", "API_KEY"):
                        if env.get(key):
                            print(env[key])
                            return True
        for value in node.values():
            if walk(value):
                return True
    elif isinstance(node, list):
        for value in node:
            if walk(value):
                return True
    return False

walk(cfg)
PY
)"
    [ -n "${BAMBUDDY_API_KEY:-}" ] && echo "Using API key from ~/.claude.json"
fi

auth_header() {
    if [ -n "${1:-}" ]; then printf 'X-API-Key: %s' "$1"; else printf 'X-Dummy: none'; fi
}

if [ -n "$LOCAL_ZIP" ]; then
    ZIP="$LOCAL_ZIP"
    [ -f "$ZIP" ] || { echo "No such file: $ZIP" >&2; exit 1; }
else
    mkdir -p "$OUT_DIR"
    ZIP="$OUT_DIR/bambuddy-prod-$(date +%Y%m%d-%H%M%S).zip"

    echo "==> Downloading backup from $PROD_URL"
    code=$(curl -sS -o "$ZIP" -w '%{http_code}' \
        -H "$(auth_header "${BAMBUDDY_API_KEY:-}")" \
        "$PROD_URL/api/v1/settings/backup")

    if [ "$code" != "200" ]; then
        rm -f "$ZIP"
        echo "Backup download failed (HTTP $code)." >&2
        case "$code" in
            401|403) echo "The API key needs the settings:backup permission. Either grant it in Settings → API Keys, or download the ZIP by hand from Settings → Backup and pass it: scripts/dev-restore.sh <file.zip>" >&2 ;;
        esac
        exit 1
    fi

    # A JSON error body would also arrive as 200 from some proxies; a real
    # backup is a ZIP and starts with PK.
    if [ "$(head -c 2 "$ZIP")" != "PK" ]; then
        echo "Downloaded file is not a ZIP:" >&2
        head -c 200 "$ZIP" >&2; echo >&2
        rm -f "$ZIP"
        exit 1
    fi
    echo "    $ZIP ($(du -h "$ZIP" | cut -f1))"
fi

if [ "$DOWNLOAD_ONLY" = true ]; then
    echo "Downloaded only, as asked. Restore with: scripts/dev-restore.sh $ZIP"
    exit 0
fi

echo "==> Checking the dev instance at $DEV_URL"
if ! curl -sSf -o /dev/null "$DEV_URL/health"; then
    echo "Dev instance is not answering. Start it first:" >&2
    echo "  docker compose up -d" >&2
    exit 1
fi

echo "==> Restoring into the dev instance"
echo "    This replaces its database and data directories."
if [ "$ASSUME_YES" = true ]; then
    echo "    --yes given, going ahead."
elif [ -t 0 ]; then
    printf '    Continue? [y/N] '
    read -r reply
    case "$reply" in y|Y|yes) ;; *) echo "Aborted."; exit 1 ;; esac
else
    echo "Not a terminal and --yes was not given; refusing to overwrite." >&2
    exit 1
fi

code=$(curl -sS -o /tmp/bambuddy-restore-response.json -w '%{http_code}' \
    -X POST \
    -H "$(auth_header "${BAMBUDDY_DEV_KEY:-}")" \
    -F "file=@${ZIP}" \
    "$DEV_URL/api/v1/settings/restore")

if [ "$code" != "200" ]; then
    echo "Restore failed (HTTP $code):" >&2
    cat /tmp/bambuddy-restore-response.json >&2; echo >&2
    exit 1
fi
cat /tmp/bambuddy-restore-response.json; echo

echo
echo "==> Restarting the dev container (restore requires it)"
# Compose ships both as a `docker compose` plugin and as a standalone binary,
# and a machine can have either. Use whichever answers.
if docker compose version >/dev/null 2>&1; then
    docker compose restart bambuddy
else
    docker-compose restart bambuddy
fi

cat <<'NOTE'

Done. The local copy is now a verbatim clone of production, which means:
  - it will connect to the same real printers over MQTT;
  - smart-plug and virtual-printer configuration came across as-is.
Review Settings before leaving it running.
NOTE
