#!/usr/bin/env bash
set -euo pipefail

install_root="${MULTI_SEARCH_INSTALL_ROOT:-$HOME/.zcode/mcp/multi-search-mcp}"
private_root="${MULTI_SEARCH_PRIVATE_ROOT:-$install_root/private}"
label="com.bri-king.family-multi-search"
plist="$HOME/Library/LaunchAgents/$label.plist"
logs="$HOME/Library/Logs/family-multi-search"
python_bin="${MULTI_SEARCH_PYTHON:-$(command -v python3)}"

if [[ ! -d "$install_root/tools" || ! -d "$private_root" || -L "$private_root" ]]; then
  printf 'remote runtime or private directory is missing\n' >&2
  exit 2
fi
mkdir -p "$HOME/Library/LaunchAgents" "$logs"
chmod 700 "$private_root" "$logs"

PLIST_PATH="$plist" INSTALL_ROOT="$install_root" PRIVATE_ROOT="$private_root" PYTHON_BIN="$python_bin" LOG_DIR="$logs" python3 - <<'PY'
import os
import plistlib
from pathlib import Path

value = {
    "Label": "com.bri-king.family-multi-search",
    "ProgramArguments": [os.environ["PYTHON_BIN"], "-m", "tools.remote_gateway"],
    "WorkingDirectory": os.environ["INSTALL_ROOT"],
    "EnvironmentVariables": {
        "MULTI_SEARCH_PRIVATE_ROOT": os.environ["PRIVATE_ROOT"],
        "PYTHONUNBUFFERED": "1",
    },
    "RunAtLoad": True,
    "KeepAlive": True,
    "ThrottleInterval": 10,
    "ProcessType": "Interactive",
    "StandardOutPath": str(Path(os.environ["LOG_DIR"]) / "stdout.log"),
    "StandardErrorPath": str(Path(os.environ["LOG_DIR"]) / "stderr.log"),
}
with open(os.environ["PLIST_PATH"], "wb") as handle:
    plistlib.dump(value, handle, sort_keys=True)
os.chmod(os.environ["PLIST_PATH"], 0o600)
PY

launchctl bootout "gui/$(id -u)/$label" >/dev/null 2>&1 || true
loaded=0
for _attempt in {1..10}; do
  if launchctl bootstrap "gui/$(id -u)" "$plist" >/dev/null 2>&1; then
    loaded=1
    break
  fi
  sleep 0.5
done
if [[ "$loaded" -ne 1 ]]; then
  launchctl bootstrap "gui/$(id -u)" "$plist"
fi
launchctl enable "gui/$(id -u)/$label"
launchctl kickstart -k "gui/$(id -u)/$label"
printf 'installed %s\n' "$label"
