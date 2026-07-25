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
rsync -a --delete --exclude 'deploy-local.sh' "$src/scripts/" "$dst/scripts/"
chmod -R go-rwx "$private"
python3 "$src/tests/test_mcp_stdio.py" --runtime "$dst/tools/multi_search_mcp.py"
