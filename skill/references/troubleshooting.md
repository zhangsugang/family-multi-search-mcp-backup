# Troubleshooting

## `401` authorization failure

Run `setup.sh` again with the assigned family Key. Each family member should use a separate Key. Do not share screenshots or logs containing the Key.

## `403` forbidden

The Key is valid but lacks the required scope. Ask the service owner to rotate or replace it.

## `429` queue timeout

The bounded full-research queue is busy. Retry later or use a narrower lookup. Twenty accepted clients do not mean twenty browser-heavy research rounds execute simultaneously.

## `partial` result

Some providers timed out, required login, or returned incomplete evidence. Use the successful citations and retain the listed unknowns rather than filling gaps from memory.

## ZCode does not show the MCP

Open the private snippet at `~/.config/multi-search-remote/zcode-mcp.json` and import its server entry through the MCP settings supported by the installed ZCode version. Configuration files do not reliably expand environment-variable templates, so the installer writes a user-only concrete header file.

## WorkBuddy

Install with its Skill directory via `./setup.sh --skill-root <directory>`. WorkBuddy can use `scripts/remote_search.py` even if it does not implement MCP Streamable HTTP.
