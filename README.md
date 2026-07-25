# Family Multi-Search MCP Backup

Standalone, secret-free backup of the family eight-source search MCP and portable ZCode/WorkBuddy Skill.

## Included

- `service/` — local stdio MCP, authenticated Streamable HTTP gateway, REST API, family-key tooling, deployment scripts, and tests.
- `skill/` — portable `multi-search-remote` Skill and standard-library REST client.
- `docs/design.md` — architecture, evidence, safety, concurrency, and deployment design.

## Public endpoints

- MCP: `https://mcp-search.bri-king.com/mcp`
- REST: `https://mcp-search.bri-king.com/v1`
- Health: `https://mcp-search.bri-king.com/healthz`

Access requires an independently issued family Bearer Key. This repository contains no family keys, provider API keys, cookies, browser profiles, storage-state, Cloudflare credentials, or private runtime files.

## Client matrix

| Client | Install | Transport |
| --- | --- | --- |
| ZCode | Remote MCP **and** `multi-search-remote` Skill | MCP Streamable HTTP at `/mcp` |
| WorkBuddy | `multi-search-remote` Skill | Bundled `remote_search.py` over REST |
| Skills-only clients | Same portable Skill | REST |

The MCP provides the actual search tools. The Skill teaches the client when to call them and how to preserve citations, conflicts, unknowns, and confidence. WorkBuddy uses the same Skill through REST when it cannot load MCP directly.

## Quick install

Download the `multi-search-remote-0.2.0` archive from Releases, extract it, then run:

```bash
./setup.sh
```

For WorkBuddy:

```bash
./setup.sh --skill-root "$HOME/.workbuddy/skills"
```

The installer asks for one family Key, stores it in a user-only configuration file, and writes a private ZCode MCP snippet at:

```text
~/.config/multi-search-remote/zcode-mcp.json
```

Import that snippet in ZCode MCP settings and start a new session. Test with “搜索 1970 文创园，并给出来源、冲突和未知项”.

## Test

```bash
cd service
python3 -m pytest tests -q
```

## Deploy

Create the private runtime directory separately, then follow `service/README.md`. Never commit or distribute `service/private/`.

Source snapshot: `zhangsugang/daima` commit `20302af`.
