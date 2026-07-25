# Family Multi-Search

Public ZCode Marketplace for a key-protected eight-source research MCP, plus a portable WorkBuddy Skill.

Repository:

```text
https://github.com/zhangsugang/family-multi-search-mcp-backup
```

The repository and Release assets contain no family Keys, provider API Keys, cookies, browser profiles, storage state, or tunnel credentials. An independently issued family Key is required to use the hosted search service.

## ZCode: install MCP + Skill as one plugin

```bash
claude plugin marketplace add zhangsugang/family-multi-search-mcp-backup
claude plugin install family-multi-search@family-multi-search \
  --config family_key='YOUR_FAMILY_KEY'
```

Restart ZCode or run `/reload-plugins` after installation.

The plugin installs both:

- MCP server: `family-multi-search`
- Skill: `multi-search-remote`

The MCP connects to `https://mcp-search.bri-king.com/mcp`. The family Key is stored as sensitive ZCode user configuration and is never part of the plugin source.

### Automatic updates

Open `/plugin` → **Marketplaces** → `family-multi-search` and enable **auto-update**. ZCode will fetch newer Marketplace/plugin versions. Manual fallback:

```bash
claude plugin marketplace update family-multi-search
claude plugin update family-multi-search@family-multi-search
```

## WorkBuddy: portable Skill

Download `multi-search-remote-0.3.0.tar.gz` or `.zip` from Releases, extract it, and run:

```bash
./setup.sh --client workbuddy
```

The installer asks for the family Key, validates it, installs the Skill under `~/.workbuddy/skills`, and writes a user-only REST configuration under `~/.config/multi-search-remote`. It does not modify ZCode.

## Service behavior

- Eight-source research with citations, conflicts, unknowns, provider status, coverage, and confidence explanations.
- Five or more family submissions may be accepted concurrently.
- Two complete research rounds run at once; additional jobs enter a bounded fair FIFO queue.
- Each Key may own one unfinished research job at a time.

Public endpoints:

- MCP: `https://mcp-search.bri-king.com/mcp`
- REST: `https://mcp-search.bri-king.com/v1`
- Health: `https://mcp-search.bri-king.com/healthz`

## Repository layout

- `.claude-plugin/marketplace.json` — Marketplace catalog.
- `plugins/family-multi-search/` — native ZCode plugin containing MCP configuration and Skill.
- `skill/` — portable ZCode/WorkBuddy Skill and REST client.
- `service/` — authenticated MCP/REST server source, deployment utilities, and tests.
- `docs/design.md` — architecture and concurrency design.

## Test

```bash
python3 -m pytest service/tests -q
claude plugin validate .
claude plugin validate plugins/family-multi-search
```

## Self-hosting

Follow `service/README.md` and create all runtime secrets separately. Never commit or distribute `service/private/`.
