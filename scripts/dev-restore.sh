#!/usr/bin/env bash
# Seed the local dev instance with a backup of the production Bambuddy.
#
#   scripts/dev-restore.sh                     # pull a fresh backup, restore locally
#   scripts/dev-restore.sh backup.zip          # restore a ZIP you already have
#   scripts/dev-restore.sh --download-only     # just fetch the ZIP
#   scripts/dev-restore.sh --yes               # no confirmation prompt
#   scripts/dev-restore.sh --keep-settings     # leave production URLs as they are
#   scripts/dev-restore.sh --no-auth           # drop the login on the local copy
#   scripts/dev-restore.sh --keep-vp           # leave the virtual printers enabled
#                                              # (they are disabled by default —
#                                              #  they cannot bind here anyway)
#
# The backup is restored verbatim — virtual printers, smart plugs and their
# settings come across exactly as production has them (external_url excepted,
# see --keep-settings). Two Bambuddy instances then talk to the same printers,
# so decide in the local UI what to switch off before letting it run unattended.
#
# Files are unpacked straight into the bind-mounted data directory rather than
# posted to /api/v1/settings/restore: that endpoint needs a logged-in session
# (API keys are denied settings:restore), and the first restore is what enables
# authentication, so the HTTP path breaks exactly when it starts being needed.
#
# Environment:
#   BAMBUDDY_PROD_URL   the production instance to copy from (required; the
#                       repo is public, so no host is hardcoded here). Read from
#                       .env, same as the compose stack, or from the environment.
#   BAMBUDDY_API_KEY    API key on the production instance. Note that API keys
#                       are denied settings:backup by design, so the download
#                       path only works for a session that is allowed it —
#                       in practice, hand the script a ZIP saved from the UI.
#   BAMBUDDY_DEV_URL    default http://localhost:8000, used for the health check
#   TS_HOSTNAME         when set, the restored copy's external_url is pointed
#                       at https://$TS_HOSTNAME instead of localhost

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# .env is where the compose stack keeps its settings, so read the handful this
# script needs from there too rather than making the shell profile carry a
# second copy. Only known keys are taken, and only when not already exported.
if [ -f .env ]; then
    for key in BAMBUDDY_PROD_URL BAMBUDDY_API_KEY BAMBUDDY_DEV_URL TS_HOSTNAME VITE_PORT; do
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
LOCALIZE=true
DISABLE_AUTH=false
# On by default: the virtual printers cannot work in a dev copy at all (their
# addresses are alias IPs on the production host), so leaving them enabled only
# buys a wall of bind errors on every start. --keep-vp opts out.
DISABLE_VPS=true
LOCAL_ZIP=""
for arg in "$@"; do
    case "$arg" in
        --download-only) DOWNLOAD_ONLY=true ;;
        -y|--yes) ASSUME_YES=true ;;
        --keep-settings) LOCALIZE=false ;;
        --no-auth) DISABLE_AUTH=true ;;
        --no-vp) DISABLE_VPS=true ;;
        --keep-vp) DISABLE_VPS=false ;;
        -h|--help) sed -n '2,12p' "$0" | cut -c3-; exit 0 ;;
        *) LOCAL_ZIP="$arg" ;;
    esac
done

# The key is already configured for the MCP server; reuse it rather than asking
# for a second copy in the shell profile. Only consulted when a download is
# actually going to happen — a restore from a local ZIP needs no key at all.
find_api_key() {
    [ -n "${BAMBUDDY_API_KEY:-}" ] && return 0
    [ -f "$HOME/.claude.json" ] || return 0
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
    export BAMBUDDY_API_KEY
}

# Compose ships both as a `docker compose` plugin and as a standalone binary,
# and a machine can have either. Use whichever answers.
compose() {
    if docker compose version >/dev/null 2>&1; then
        docker compose "$@"
    else
        docker-compose "$@"
    fi
}

auth_header() {
    if [ -n "${1:-}" ]; then printf 'X-API-Key: %s' "$1"; else printf 'X-Dummy: none'; fi
}

if [ -n "$LOCAL_ZIP" ]; then
    ZIP="$LOCAL_ZIP"
    [ -f "$ZIP" ] || { echo "No such file: $ZIP" >&2; exit 1; }
else
    mkdir -p "$OUT_DIR"
    ZIP="$OUT_DIR/bambuddy-prod-$(date +%Y%m%d-%H%M%S).zip"

    find_api_key
    echo "==> Downloading backup from $PROD_URL"
    code=$(curl -sS -o "$ZIP" -w '%{http_code}' \
        -H "$(auth_header "${BAMBUDDY_API_KEY:-}")" \
        "$PROD_URL/api/v1/settings/backup")

    if [ "$code" != "200" ]; then
        rm -f "$ZIP"
        echo "Backup download failed (HTTP $code)." >&2
        case "$code" in
            401|403) echo "API keys cannot download backups — settings:backup and settings:restore are on the API-key denylist in backend/app/core/auth.py, so no combination of key permissions authorises this. Download the ZIP from Settings → Backup in the browser and pass it instead:" >&2
                     echo "  scripts/dev-restore.sh <file.zip>" >&2 ;;
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

echo "==> Restoring into the dev copy"
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

# The unpacking is done on the host, not through POST /settings/restore.
# That endpoint needs a logged-in session — settings:restore is denied to API
# keys — and the very first restore is what turns authentication on, so the
# HTTP path stops working exactly when you start needing it. /app/data is a
# bind mount, so writing the files directly is both simpler and auth-free; the
# set of things replaced mirrors restore_backup() in
# backend/app/api/routes/settings.py.
DATA_DIR="dev-data/data"

echo "==> Stopping the backend"
compose stop bambuddy >/dev/null 2>&1 || true

python3 - "$ZIP" "$DATA_DIR" <<'PYEOF'
import os
import shutil
import sys
import zipfile
from pathlib import Path

zip_path, data_dir = Path(sys.argv[1]), Path(sys.argv[2])
data_dir.mkdir(parents=True, exist_ok=True)

# Same five directories the restore endpoint replaces, plus the two files.
DIRS = ("archive", "virtual_printer", "plate_calibration", "icons", "projects")

with zipfile.ZipFile(zip_path) as zf:
    names = zf.namelist()
    if "bambuddy.db" not in names:
        sys.exit("Invalid backup: no bambuddy.db inside")
    # Reject path-traversal entries before extracting anything (ZipSlip).
    root = data_dir.resolve()
    for name in names:
        if not (root / name).resolve().is_relative_to(root):
            sys.exit(f"Invalid backup: unsafe path {name!r}")

    # A stale WAL alongside a swapped database silently resurrects rows from
    # the copy being replaced, so both sidecars go before the file does.
    for suffix in ("", "-wal", "-shm"):
        (data_dir / f"bambuddy.db{suffix}").unlink(missing_ok=True)

    for name in ("bambuddy.db", ".mfa_encryption_key"):
        if name in names:
            with zf.open(name) as src, open(data_dir / name, "wb") as dst:
                shutil.copyfileobj(src, dst)
            print(f"  restored {name}")

    for directory in DIRS:
        members = [n for n in names if n.startswith(f"{directory}/") and not n.endswith("/")]
        if not members:
            continue
        dest = data_dir / directory
        if dest.exists():
            shutil.rmtree(dest)
        for member in members:
            target = data_dir / member
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
        print(f"  restored {directory}/ ({len(members)} files)")

os.chmod(data_dir / ".mfa_encryption_key", 0o600) if (data_dir / ".mfa_encryption_key").exists() else None
PYEOF

# The backup carries the production instance's own addresses. external_url is
# what notification images and label QR codes are built from, so left alone the
# dev copy hands out links into production.
if [ "$LOCALIZE" = true ]; then
    new_url="${TS_HOSTNAME:+https://$TS_HOSTNAME}"
    : "${new_url:=http://localhost:${VITE_PORT:-5173}}"
    echo
    echo "==> Pointing external_url at this instance: $new_url"
    python3 - "$DATA_DIR/bambuddy.db" "$new_url" <<'PYEOF'
import sqlite3, sys

db = sqlite3.connect(sys.argv[1])
url = sys.argv[2]
old = db.execute("select value from settings where key='external_url'").fetchone()
db.execute("update settings set value=? where key='external_url'", (url,))
db.commit()
print(f"  external_url: {old[0] if old else '(unset)'} -> {url}")

# Left alone, because what they should be depends on where the stack runs.
for key in ("ha_url", "orcaslicer_api_url", "bambu_studio_api_url"):
    row = db.execute("select value from settings where key=?", (key,)).fetchone()
    if row and row[0]:
        print(f"  left as-is: {key} = {row[0]}")
PYEOF
fi

# Opt-in: the restored copy inherits production's users, so the dev instance
# asks for a login it is inconvenient to type on every rebuild. Turning it off
# means anything that can reach this instance — every device on the tailnet
# when the sidecar is up — can drive the real printers, so it stays a choice.
if [ "$DISABLE_AUTH" = true ]; then
    echo
    echo "==> Disabling authentication on the local copy"
    python3 - "$DATA_DIR/bambuddy.db" <<'PYEOF'
import sqlite3, sys

db = sqlite3.connect(sys.argv[1])
db.execute("update settings set value='false' where key='auth_enabled'")
db.commit()
print("  auth_enabled -> false (local copy only)")
PYEOF
fi

# Default behaviour. The virtual printers bind LAN addresses that, in this
# setup, are alias IPs on the production host — .2/.3/.4 and the Home Assistant
# box answer with one MAC. A second instance cannot have them, so every dev
# start otherwise fails to bind each VP and buries the interesting log lines.
if [ "$DISABLE_VPS" = true ]; then
    echo
    echo "==> Disabling the virtual printers on the local copy"
    python3 - "$DATA_DIR/bambuddy.db" <<'PYEOF'
import sqlite3, sys

db = sqlite3.connect(sys.argv[1])
rows = db.execute("select name from virtual_printers where enabled=1").fetchall()
db.execute("update virtual_printers set enabled=0")
db.commit()
for (name,) in rows:
    print(f"  disabled: {name}")
if not rows:
    print("  none were enabled")
PYEOF
fi

echo
echo "==> Starting the backend"
compose up -d bambuddy

cat <<'NOTE'

Done. The local copy is now a verbatim clone of production, which means:
  - it will connect to the same real printers over MQTT;
  - smart-plug and virtual-printer configuration came across as-is.
Review Settings before leaving it running.
NOTE
