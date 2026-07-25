#!/usr/bin/env bash
set -euo pipefail

repository="https://github.com/zhangsugang/family-multi-search-mcp-backup.git"
base_url="https://mcp-search.bri-king.com"
zcode_root="${HOME}/.zcode/cli"
plugin_root="$zcode_root/plugins"
config_path="$zcode_root/config.json"

if [[ ! -f "$config_path" ]]; then
  printf 'ZCode configuration not found: %s\n' "$config_path" >&2
  printf 'Install and open ZCode once, then run this installer again.\n' >&2
  exit 2
fi
for command in git python3; do
  if ! command -v "$command" >/dev/null 2>&1; then
    printf 'required command is unavailable: %s\n' "$command" >&2
    exit 2
  fi
done

key="${MULTI_SEARCH_KEY:-}"
if [[ -z "$key" ]]; then
  printf 'Family access key: ' >&2
  IFS= read -r -s key
  printf '\n' >&2
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
    os.environ["MULTI_SEARCH_URL"] + "/v1/providers/status",
    headers={
        "Accept": "application/json",
        "Authorization": "Bearer " + os.environ["MULTI_SEARCH_KEY"],
        "User-Agent": "family-multi-search-zcode-installer/0.3.2",
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

temporary="$(mktemp -d "${TMPDIR:-/tmp}/family-multi-search-install.XXXXXX")"
cleanup() { rm -rf "$temporary"; }
trap cleanup EXIT

git clone --depth 1 "$repository" "$temporary/repository" >/dev/null
version="$(python3 - "$temporary/repository" <<'PY'
import json
import sys
from pathlib import Path
root = Path(sys.argv[1])
value = json.loads(
    (root / "plugins/family-multi-search/.zcode-plugin/plugin.json").read_text()
)
print(value["version"])
PY
)"

marketplace="$plugin_root/marketplaces/family-multi-search"
cache="$plugin_root/cache/family-multi-search/family-multi-search/$version"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="$HOME/.zcode/backups/family-multi-search-installer-$stamp"
mkdir -m 700 -p "$backup"

for path in \
  "$config_path" \
  "$plugin_root/known_marketplaces.json" \
  "$plugin_root/installed_plugins.json"; do
  if [[ -f "$path" ]]; then
    cp -p "$path" "$backup/$(basename "$path")"
    chmod 600 "$backup/$(basename "$path")"
  fi
done
if [[ -e "$marketplace" ]]; then
  mv "$marketplace" "$backup/marketplace"
fi
if [[ -e "$cache" ]]; then
  mkdir -p "$backup/cache"
  mv "$cache" "$backup/cache/$version"
fi

mkdir -p "$(dirname "$marketplace")" "$(dirname "$cache")"
mv "$temporary/repository" "$marketplace"
cp -R "$marketplace/plugins/family-multi-search" "$cache"
chmod 755 "$cache/skills/multi-search-remote/scripts/zcode_mcp_proxy.py"

ZCODE_CONFIG="$config_path" \
KNOWN_MARKETPLACES="$plugin_root/known_marketplaces.json" \
INSTALLED_PLUGINS="$plugin_root/installed_plugins.json" \
INSTALL_PATH="$cache" \
PLUGIN_VERSION="$version" \
ACCESS_KEY="$key" \
BASE_URL="$base_url" \
python3 - <<'PY'
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def load(path: Path, default: dict) -> dict:
    if not path.is_file():
        return default
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"invalid JSON object: {path}")
    return value


def atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}-", dir=path.parent, text=True
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
version = os.environ["PLUGIN_VERSION"]
install_path = os.environ["INSTALL_PATH"]

known_path = Path(os.environ["KNOWN_MARKETPLACES"])
known = load(known_path, {"version": 1, "marketplaces": []})
marketplaces = [
    item
    for item in known.setdefault("marketplaces", [])
    if item.get("id") != "family-multi-search"
]
marketplaces.append(
    {
        "id": "family-multi-search",
        "source": {
            "source": "github",
            "repo": "zhangsugang/family-multi-search-mcp-backup",
        },
        "name": "family-multi-search",
        "description": "Key-protected family eight-source search MCP and Skill",
        "addedAt": now,
        "pluginCount": 1,
        "lastUpdated": now,
    }
)
known["marketplaces"] = marketplaces
atomic(known_path, known)

installed_path = Path(os.environ["INSTALLED_PLUGINS"])
installed = load(installed_path, {"version": 1, "plugins": []})
plugins = [
    item
    for item in installed.setdefault("plugins", [])
    if item.get("id") != "family-multi-search@family-multi-search"
]
plugins.append(
    {
        "id": "family-multi-search@family-multi-search",
        "name": "family-multi-search",
        "marketplace": "family-multi-search",
        "version": version,
        "installPath": install_path,
        "installedAt": now,
        "updatedAt": now,
        "scope": "user",
        "source": "./plugins/family-multi-search",
    }
)
installed["plugins"] = plugins
atomic(installed_path, installed)

config_path = Path(os.environ["ZCODE_CONFIG"])
config = load(config_path, {})
config.setdefault("plugins", {}).setdefault("enabledPlugins", {})[
    "family-multi-search@family-multi-search"
] = True
atomic(config_path, config)

private_root = Path.home() / ".config" / "multi-search-remote"
if private_root.is_symlink():
    raise SystemExit("private configuration directory must not be a symlink")
private_root.mkdir(parents=True, exist_ok=True, mode=0o700)
os.chmod(private_root, 0o700)
private_path = private_root / "config.json"
if private_path.is_symlink():
    raise SystemExit("private configuration file must not be a symlink")
atomic(
    private_path,
    {
        "base_url": os.environ["BASE_URL"],
        "access_key": os.environ["ACCESS_KEY"],
    },
)
PY

printf '\nInstalled ZCode plugin: family-multi-search@family-multi-search\n'
printf 'Version: %s\n' "$version"
printf 'MCP: family-multi-search (stdio)\n'
printf 'Skill: multi-search-remote\n'
printf 'Private Key config: %s\n' "$HOME/.config/multi-search-remote/config.json"
printf 'Backup: %s\n' "$backup"
printf '\nFully quit and reopen ZCode, or run /reload-plugins.\n'
printf 'Then check Settings → Plugin Management → Installed → family-multi-search.\n'
