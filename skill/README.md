# Multi Search Remote 0.3.4

Portable fallback client for the public `family-multi-search` ZCode plugin and for WorkBuddy/Skills-only clients.

- Public repository: `https://github.com/zhangsugang/family-multi-search-mcp-backup`
- MCP: `https://mcp-search.bri-king.com/mcp`
- REST: `https://mcp-search.bri-king.com/v1`
- Providers: Tavily, Exa through Agent-Reach, Doubao, Yuanbao, Wenxin, Grok, Gemini, Qianwen
- Grok: one X/Twitter-specialized query per research round; no general lane
- Tavily: private five-Key server-side rotation; no provider Key is shipped to clients
- No access key, provider credential, cookie, browser profile, or storage state is included.

## ZCode installation

First configure the family Key from the extracted release directory:

```bash
./setup.sh --client zcode
```

The installer securely prompts for the Key, verifies it, and writes only:

```text
~/.config/multi-search-remote/config.json
```

The directory has mode `0700` and the file has mode `0600`.

Then open ZCode:

```text
Settings → Plugin Management → Discover → +
```

Add this GitHub Marketplace:

```text
https://github.com/zhangsugang/family-multi-search-mcp-backup
```

Install `family-multi-search`, then restart ZCode or run `/reload-plugins`. Do not use `claude plugin` commands: those install into Claude Code, not ZCode.

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

One family Key may be shared by up to 10 bound public IP addresses. Request isolation and job ownership are applied per bound address, not across the whole Key. Each address may hold up to 20 unfinished jobs. Active research is mode-bounded: up to 20 `fast`, 5 `balanced`, or 2 `deep` rounds; excess accepted jobs expose an explicit queue position and are retrieved with `get_research_result`. Logical browser slots are not equivalent to independent provider-account capacity.
