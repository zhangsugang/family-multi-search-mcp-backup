#!/usr/bin/env bash
set -euo pipefail

source_dir="$(cd "$(dirname "$0")" && pwd)"
client="zcode"
skill_root=""
config_root="${HOME}/.config/multi-search-remote"
base_url="https://mcp-search.bri-king.com"
non_interactive=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --client) client="$2"; shift 2 ;;
    --skill-root) skill_root="$2"; shift 2 ;;
    --config-root) config_root="$2"; shift 2 ;;
    --url) base_url="${2%/}"; shift 2 ;;
    --non-interactive) non_interactive=1; shift ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

case "$client" in
  zcode|workbuddy|skill-only) ;;
  *) printf 'client must be zcode, workbuddy, or skill-only\n' >&2; exit 2 ;;
esac

key="${MULTI_SEARCH_KEY:-}"
if [[ -z "$key" && "$non_interactive" -eq 0 ]]; then
  printf 'Family access key: ' >&2
  IFS= read -r -s key
  printf '\n' >&2
fi
if [[ -z "$key" ]]; then
  printf 'MULTI_SEARCH_KEY is required in non-interactive mode\n' >&2
  exit 2
fi
if [[ ! "$key" =~ ^fms_[a-z0-9]{8}_[A-Za-z0-9_-]{32,}$ ]]; then
  printf 'invalid family access key format\n' >&2
  exit 2
fi

MULTI_SEARCH_KEY="$key" MULTI_SEARCH_URL="$base_url" python3 - <<'PY'
import json
import os
import urllib.error
import urllib.request

request = urllib.request.Request(
    os.environ["MULTI_SEARCH_URL"].rstrip("/") + "/v1/providers/status",
    headers={
        "Accept": "application/json",
        "Authorization": "Bearer " + os.environ["MULTI_SEARCH_KEY"],
        "User-Agent": "multi-search-remote-installer/0.3.4",
    },
)
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        value = json.loads(response.read().decode("utf-8"))
except (urllib.error.URLError, ValueError) as exc:
    raise SystemExit(f"family Key or service verification failed: {exc}")
if value.get("status") != "ready":
    raise SystemExit("family search service is not ready")
PY

if [[ -L "$config_root" ]]; then
  printf 'config root must not be a symlink\n' >&2
  exit 2
fi
if [[ ! -e "$config_root" ]]; then
  mkdir -m 700 -p "$config_root"
fi
CONFIG_ROOT="$config_root" python3 - <<'PY'
import os
import stat
from pathlib import Path

path = Path(os.environ["CONFIG_ROOT"])
metadata = path.stat()
if not path.is_dir() or metadata.st_uid != os.getuid():
    raise SystemExit("config root must be a user-owned directory")
if stat.S_IMODE(metadata.st_mode) & 0o077:
    raise SystemExit("config root must have mode 0700")
PY
CONFIG_PATH="$config_root/config.json" BASE_URL="$base_url" ACCESS_KEY="$key" python3 - <<'PY'
import json
import os
import tempfile
from pathlib import Path

path = Path(os.environ["CONFIG_PATH"])
if path.is_symlink():
    raise SystemExit(f"refusing symlinked private config: {path}")
descriptor, temporary = tempfile.mkstemp(prefix=".config-", dir=path.parent, text=True)
try:
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump({
            "base_url": os.environ["BASE_URL"],
            "access_key": os.environ["ACCESS_KEY"],
        }, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    os.chmod(path, 0o600)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY

if [[ "$client" == "zcode" ]]; then
  printf 'Private ZCode MCP config: %s\n' "$config_root/config.json"
  printf 'In ZCode open Settings → Plugin Management → Discover → +.\n'
  printf 'Add GitHub repository: zhangsugang/family-multi-search-mcp-backup\n'
  printf 'Install family-multi-search, then restart ZCode or run /reload-plugins.\n'
  exit 0
fi

if [[ -z "$skill_root" ]]; then
  if [[ "$client" == "workbuddy" ]]; then
    skill_root="$HOME/.workbuddy/skills"
  else
    printf -- '--skill-root is required for skill-only mode\n' >&2
    exit 2
  fi
fi

destination="$skill_root/multi-search-remote"
mkdir -p "$destination"
rsync -a --delete \
  --exclude '.git/' --exclude '__pycache__/' --exclude '*.pyc' \
  "$source_dir/" "$destination/"
chmod 755 \
  "$destination/setup.sh" \
  "$destination/scripts/remote_search.py" \
  "$destination/scripts/zcode_mcp_proxy.py"

printf 'Skill installed: %s\n' "$destination"
printf 'Private REST config: %s\n' "$config_root/config.json"
printf 'WorkBuddy uses Skill + REST; no ZCode configuration was modified.\n'
