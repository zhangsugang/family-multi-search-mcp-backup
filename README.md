# Family Multi-Search

Public ZCode Marketplace for a key-protected eight-source research MCP, plus a portable WorkBuddy Skill.

```text
https://github.com/zhangsugang/family-multi-search-mcp-backup
```

No family Key, provider credential, cookie, browser profile, or tunnel credential is committed to this repository or its Release assets.

## ZCode installation

### 1. Configure the family Key

Download and extract `multi-search-remote-0.3.2.tar.gz` or `.zip` from Releases, then run:

```bash
./setup.sh --client zcode
```

The installer securely prompts for the Key, validates it, and writes:

```text
~/.config/multi-search-remote/config.json
```

The directory has mode `0700`; the private file has mode `0600`.

### 2. Install the native ZCode plugin

Open:

```text
Settings → Plugin Management → Discover → +
```

Add the GitHub repository:

```text
https://github.com/zhangsugang/family-multi-search-mcp-backup
```

Install `family-multi-search`, then restart ZCode or run `/reload-plugins`.

**Do not use `claude plugin` commands for ZCode.** Those commands install into Claude Code's `~/.claude` directory and the plugin will not appear in ZCode.

### 3. Verify

Open:

```text
Settings → Plugin Management → Installed → family-multi-search
```

Confirm that the plugin is enabled and MCP `family-multi-search` is loaded. It exposes:

- `search_once`
- `research`
- `get_research_result`
- `continue_research`
- `provider_status`

The plugin uses a bundled stdio MCP bridge. The family Key stays in the private user configuration file and is never placed in the plugin cache or GitHub.

## Updating in ZCode

Open **Settings → Plugin Management**, refresh the `family-multi-search` Marketplace, and install the offered update. Restart ZCode or run `/reload-plugins` afterward.

## WorkBuddy

From the same v0.3.2 release directory:

```bash
./setup.sh --client workbuddy
```

This installs the Skill under `~/.workbuddy/skills` and uses the same private REST configuration. It does not modify ZCode.

## Service behavior

- Eight-source research with citations, conflicts, unknowns, coverage, provider status, and confidence explanations.
- Each Key may bind up to 10 public IP addresses; only irreversible address digests are stored.
- Request isolation and one-unfinished-job limits apply per bound address.
- Two complete research rounds run simultaneously; additional accepted jobs enter a fair FIFO queue.

Public endpoints:

- MCP: `https://mcp-search.bri-king.com/mcp`
- REST: `https://mcp-search.bri-king.com/v1`
- Health: `https://mcp-search.bri-king.com/healthz`

## Test

```bash
python3 -m pytest service/tests -q
claude plugin validate .
claude plugin validate plugins/family-multi-search
```

The `claude plugin validate` commands above are only manifest validators; they are not the ZCode installation commands.
