# Multi Search Remote

Portable client package for the family eight-source search service.

- MCP: `https://mcp-search.bri-king.com/mcp`
- REST: `https://mcp-search.bri-king.com/v1`
- Providers: Tavily, Exa, Doubao, Yuanbao, Wenxin, Grok, Gemini, Qianwen
- The archive contains no access key, provider credential, cookie, browser profile, or storage state.

## Install

```bash
tar -xzf multi-search-remote.tar.gz
cd multi-search-remote
./setup.sh
```

The installer asks for the family Key, stores it in a user-only file, installs the Skill, and writes a private ZCode MCP configuration snippet. It does not edit unknown client configuration files automatically.

Default locations:

```text
~/.zcode/skills/multi-search-remote/
~/.config/multi-search-remote/config.json
~/.config/multi-search-remote/zcode-mcp.json
```

For WorkBuddy or another Skills-only client, choose its Skill root:

```bash
./setup.sh --skill-root "$HOME/.workbuddy/skills"
```

For unattended setup, pass the key through the environment instead of a command-line argument:

```bash
MULTI_SEARCH_KEY='fms_...' ./setup.sh --non-interactive
```

## Test

```bash
python3 ~/.zcode/skills/multi-search-remote/scripts/remote_search.py status
python3 ~/.zcode/skills/multi-search-remote/scripts/remote_search.py research \
  --query '1970文创园' --wait
```

The remote service can accept multiple family clients, but complete eight-source research is deliberately admitted at a lower active concurrency to protect browser-backed providers. Queued capacity is not the same as simultaneous full-research capacity.
