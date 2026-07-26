#!/usr/bin/env bash
set -euo pipefail

repository="${MULTI_SEARCH_REPOSITORY:-https://github.com/zhangsugang/family-multi-search-mcp-backup.git}"
base_url="${MULTI_SEARCH_URL:-https://mcp-search.bri-king.com}"
zcode_root="${HOME}/.zcode/cli"
plugin_root="$zcode_root/plugins"
config_path="$zcode_root/config.json"
python_bin="${PYTHON_BIN:-python3}"

if [[ ! -f "$config_path" ]]; then
  printf 'ZCode configuration not found: %s\n' "$config_path" >&2
  printf 'Install and open ZCode once, then run this installer again.\n' >&2
  exit 2
fi
for command in git "$python_bin"; do
  if ! command -v "$command" >/dev/null 2>&1; then
    printf 'required command is unavailable: %s\n' "$command" >&2
    exit 2
  fi
done
if ! "$python_bin" -c 'import sys; raise SystemExit(sys.version_info < (3, 9))'; then
  printf 'Python 3.9 or newer is required; set PYTHON_BIN to a compatible interpreter.\n' >&2
  exit 2
fi

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

MULTI_SEARCH_KEY="$key" MULTI_SEARCH_URL="$base_url" "$python_bin" - <<'PY'
import json
import os
import urllib.error
import urllib.request

request = urllib.request.Request(
    os.environ["MULTI_SEARCH_URL"] + "/v1/providers/status",
    headers={
        "Accept": "application/json",
        "Authorization": "Bearer " + os.environ["MULTI_SEARCH_KEY"],
        "User-Agent": "family-multi-search-zcode-installer/0.3.4",
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
marketplace=""
cache=""
backup=""
committed=0
finish() {
  status=$?
  if [[ "$status" -ne 0 && -n "$backup" && -d "$backup" ]]; then
    rm -rf "${marketplace:-}" "${cache:-}"
    [[ ! -e "$backup/marketplace" ]] || mv "$backup/marketplace" "$marketplace"
    [[ ! -e "$backup/cache-current" ]] || mv "$backup/cache-current" "$cache"
    rm -f "$config_path" "$plugin_root/known_marketplaces.json" \
      "$plugin_root/installed_plugins.json" \
      "$HOME/.config/multi-search-remote/config.json"
    [[ ! -f "$backup/zcode-config.json" ]] || cp -p "$backup/zcode-config.json" "$config_path"
    [[ ! -f "$backup/known-marketplaces.json" ]] || cp -p "$backup/known-marketplaces.json" "$plugin_root/known_marketplaces.json"
    [[ ! -f "$backup/installed-plugins.json" ]] || cp -p "$backup/installed-plugins.json" "$plugin_root/installed_plugins.json"
    if [[ -f "$backup/private-config.json" ]]; then
      mkdir -m 700 -p "$HOME/.config/multi-search-remote"
      cp -p "$backup/private-config.json" "$HOME/.config/multi-search-remote/config.json"
    fi
    if [[ -e "$backup/legacy-skill" ]]; then
      mkdir -p "$HOME/.zcode/skills"
      mv "$backup/legacy-skill" "$HOME/.zcode/skills/multi-search"
    fi
  fi
  rm -rf "$temporary"
  trap - EXIT
  exit "$status"
}
trap finish EXIT

git clone --depth 1 "$repository" "$temporary/repository" >/dev/null
version="$("$python_bin" - "$temporary/repository" <<'PY'
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

[[ ! -f "$config_path" ]] || cp -p "$config_path" "$backup/zcode-config.json"
[[ ! -f "$plugin_root/known_marketplaces.json" ]] || cp -p "$plugin_root/known_marketplaces.json" "$backup/known-marketplaces.json"
[[ ! -f "$plugin_root/installed_plugins.json" ]] || cp -p "$plugin_root/installed_plugins.json" "$backup/installed-plugins.json"
[[ ! -f "$HOME/.config/multi-search-remote/config.json" ]] || cp -p "$HOME/.config/multi-search-remote/config.json" "$backup/private-config.json"
chmod 600 "$backup"/*.json 2>/dev/null || true

[[ ! -e "$marketplace" ]] || mv "$marketplace" "$backup/marketplace"
[[ ! -e "$cache" ]] || mv "$cache" "$backup/cache-current"
if [[ -e "$HOME/.zcode/skills/multi-search" ]]; then
  mv "$HOME/.zcode/skills/multi-search" "$backup/legacy-skill"
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
"$python_bin" - <<'PY'
import json
import os
import tempfile
from datetime import datetime, timezone
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


now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
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
legacy_servers = config.get("mcp", {}).get("servers", {})
if isinstance(legacy_servers, dict):
    legacy_servers.pop("multi-search", None)
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

DOCTOR_PROXY="$cache/skills/multi-search-remote/scripts/zcode_mcp_proxy.py" \
ZCODE_CONFIG="$config_path" \
"$python_bin" - <<'PY'
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

proxy = Path(os.environ["DOCTOR_PROXY"])
private = Path.home() / ".config" / "multi-search-remote" / "config.json"
config = json.loads(Path(os.environ["ZCODE_CONFIG"]).read_text(encoding="utf-8"))
enabled = config.get("plugins", {}).get("enabledPlugins", {})
if enabled.get("family-multi-search@family-multi-search") is not True:
    raise SystemExit("doctor failed: plugin is not enabled")
if stat.S_IMODE(private.stat().st_mode) != 0o600:
    raise SystemExit("doctor failed: private config mode is not 0600")
messages = [
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-03-26"},
    },
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "provider_status", "arguments": {}},
    },
]
process = subprocess.run(
    [sys.executable, str(proxy)],
    input="".join(json.dumps(item) + "\n" for item in messages),
    text=True,
    capture_output=True,
    timeout=45,
)
if process.returncode != 0:
    raise SystemExit("doctor failed: MCP proxy did not start")
responses = {
    item.get("id"): item
    for item in (
        json.loads(line) for line in process.stdout.splitlines() if line.strip()
    )
}
if responses.get(1, {}).get("result", {}).get("serverInfo", {}).get("name") != "family-multi-search":
    raise SystemExit("doctor failed: MCP initialize")
tools = responses.get(2, {}).get("result", {}).get("tools", [])
names = {item.get("name") for item in tools}
required = {
    "search_once",
    "research_round",
    "research",
    "get_research_result",
    "continue_research",
    "provider_status",
}
if not required.issubset(names):
    raise SystemExit("doctor failed: tools/list")
status = responses.get(3, {}).get("result", {}).get("structuredContent", {})
if status.get("status") != "ready":
    raise SystemExit("doctor failed: provider_status")
print("PASS  plugin registered and enabled")
print("PASS  private config mode 0600")
print("PASS  MCP initialize")
print("PASS  tools/list: 6 tools")
print("PASS  provider_status: ready")
PY

committed=1

printf '\nInstalled ZCode plugin: family-multi-search@family-multi-search\n'
printf 'Version: %s\n' "$version"
printf 'MCP: family-multi-search (stdio)\n'
printf 'Skill: multi-search-remote\n'
printf 'Private Key config: %s\n' "$HOME/.config/multi-search-remote/config.json"
printf 'Backup: %s\n' "$backup"
printf '\nFully quit and reopen ZCode, or run /reload-plugins.\n'
printf 'Then check Settings → Plugin Management → Installed → family-multi-search.\n'
