#!/usr/bin/env bash
set -euo pipefail

src="$(cd "$(dirname "$0")/.." && pwd)"
dst="${MULTI_SEARCH_INSTALL_ROOT:-$HOME/.zcode/mcp/multi-search-mcp}"
private="$dst/private"

if [[ -z "$dst" || "$dst" == "/" || "$dst" == "$src" ]]; then
  printf 'unsafe install root\n' >&2
  exit 2
fi
if [[ ! -d "$private" || -L "$private" ]]; then
  printf 'private runtime directory is missing or unsafe\n' >&2
  exit 2
fi

python3 -m pytest "$src/tests" -q
mkdir -p "$dst/tools" "$dst/config" "$dst/scripts"
rsync -a --delete --exclude '__pycache__/' --exclude '*.pyc' "$src/tools/" "$dst/tools/"
rsync -a --delete "$src/config/" "$dst/config/"
rsync -a --delete --exclude 'deploy-local.sh' --exclude 'deploy-remote.sh' "$src/scripts/" "$dst/scripts/"
chmod -R go-rwx "$private"
MULTI_SEARCH_INSTALL_ROOT="$dst" "$src/scripts/install-remote-launchagent.sh"
ready=0
for _attempt in {1..40}; do
  if curl -fsS --max-time 2 http://127.0.0.1:8765/healthz >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.5
done
if [[ "$ready" -ne 1 ]]; then
  printf 'remote service did not become healthy; inspect %s\n' \
    "$HOME/Library/Logs/family-multi-search/stderr.log" >&2
  exit 1
fi
python3 "$src/scripts/remote_smoke_check.py" --url http://127.0.0.1:8765/mcp
