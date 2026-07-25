#!/usr/bin/env bash
set -euo pipefail

source_dir="$(cd "$(dirname "$0")" && pwd)"
skill_root="${HOME}/.zcode/skills"
config_root="${HOME}/.config/multi-search-remote"
base_url="https://mcp-search.bri-king.com"
non_interactive=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skill-root) skill_root="$2"; shift 2 ;;
    --config-root) config_root="$2"; shift 2 ;;
    --url) base_url="${2%/}"; shift 2 ;;
    --non-interactive) non_interactive=1; shift ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

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

destination="$skill_root/multi-search-remote"
mkdir -p "$destination"
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
rsync -a --delete \
  --exclude '.git/' --exclude '__pycache__/' --exclude '*.pyc' \
  "$source_dir/" "$destination/"
chmod 755 "$destination/setup.sh" "$destination/scripts/remote_search.py"

CONFIG_PATH="$config_root/config.json" MCP_PATH="$config_root/zcode-mcp.json" BASE_URL="$base_url" ACCESS_KEY="$key" python3 - <<'PY'
import json
import os
import tempfile
from pathlib import Path

config = Path(os.environ["CONFIG_PATH"])
mcp = Path(os.environ["MCP_PATH"])
for path in (config, mcp):
    if path.is_symlink():
        raise SystemExit(f"refusing symlinked private config: {path}")

def atomic_json(path, value):
    descriptor, temporary = tempfile.mkstemp(prefix=".config-", dir=path.parent, text=True)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

atomic_json(config, {
    "base_url": os.environ["BASE_URL"],
    "access_key": os.environ["ACCESS_KEY"],
})
atomic_json(mcp, {
    "mcpServers": {
        "family-multi-search": {
            "url": os.environ["BASE_URL"] + "/mcp",
            "headers": {"Authorization": "Bearer " + os.environ["ACCESS_KEY"]},
        }
    }
})
PY

MULTI_SEARCH_KEY="$key" MULTI_SEARCH_URL="$base_url" \
  python3 "$destination/scripts/remote_search.py" --json status >/dev/null
printf 'Skill installed: %s\n' "$destination"
printf 'Private client config: %s\n' "$config_root/config.json"
printf 'Private ZCode MCP snippet: %s\n' "$config_root/zcode-mcp.json"
printf 'Import the MCP snippet through your ZCode version MCP settings, then start a new session.\n'
