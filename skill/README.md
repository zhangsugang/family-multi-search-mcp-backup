# Multi Search Remote 0.3.0

Portable fallback client for the public `family-multi-search` ZCode plugin and for WorkBuddy/Skills-only clients.

- Public repository: `https://github.com/zhangsugang/family-multi-search-mcp-backup`
- MCP: `https://mcp-search.bri-king.com/mcp`
- REST: `https://mcp-search.bri-king.com/v1`
- Providers: Tavily, Exa, Doubao, Yuanbao, Wenxin, Grok, Gemini, Qianwen
- No access key, provider credential, cookie, browser profile, or storage state is included.

## ZCode: recommended plugin installation

From the extracted release directory:

```bash
./setup.sh
```

The installer securely prompts for the family Key, verifies it, adds the public GitHub Marketplace, and installs the plugin containing both MCP and Skill.

Equivalent direct commands:

```bash
claude plugin marketplace add zhangsugang/family-multi-search-mcp-backup
claude plugin install family-multi-search@family-multi-search \
  --config family_key='YOUR_FAMILY_KEY'
```

Then restart ZCode or run `/reload-plugins`.

Enable updates once: open `/plugin` → **Marketplaces** → `family-multi-search` → **Enable auto-update**. ZCode checks the GitHub Marketplace at startup.

Installed components:

```text
Plugin: family-multi-search
MCP: family-multi-search
Skill: multi-search-remote
```

## WorkBuddy

```bash
./setup.sh --client workbuddy
```

This installs the Skill under `~/.workbuddy/skills` and configures the bundled REST client. It does not modify ZCode.

Test:

```bash
python3 ~/.workbuddy/skills/multi-search-remote/scripts/remote_search.py status
python3 ~/.workbuddy/skills/multi-search-remote/scripts/remote_search.py \
  research --query '1970文创园' --wait
```

## Capacity

Five different family users may submit research simultaneously. Two complete eight-source rounds run at once; the remaining accepted jobs wait in a fair queue and are retrieved with `get_research_result`. Logical browser slots are not equivalent to independent provider-account capacity.
