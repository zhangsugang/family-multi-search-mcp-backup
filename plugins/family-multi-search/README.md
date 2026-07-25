# Family Multi Search ZCode Plugin

This native ZCode plugin bundles:

- stdio MCP server `family-multi-search`;
- Skill `multi-search-remote`;
- a standard-library REST bridge to the authenticated remote search service.

The easiest installation method is one command. It securely prompts for the family Key:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/zhangsugang/family-multi-search-mcp-backup/main/install-zcode.sh)"
```

The installer registers the Marketplace and plugin in the actual ZCode directories, writes the Key only to a private `0600` file, and creates a reversible backup.

The plugin does not embed a family Key. For manual installation, configure the Key first with the portable release installer:

```bash
./setup.sh --client zcode
```

The Key is written to the user-only file:

```text
~/.config/multi-search-remote/config.json
```

Then install the plugin in ZCode:

```text
Settings → Plugin Management → Discover → +
```

Add:

```text
https://github.com/zhangsugang/family-multi-search-mcp-backup
```

Install `family-multi-search`, then restart ZCode or run `/reload-plugins`. Do not use `claude plugin` commands; they install into Claude Code rather than ZCode.

One family Key may bind up to 10 public IP addresses. Request and research-job limits are isolated by bound address, while the service continues to run two complete research rounds at once and queues additional accepted work fairly.

Seven providers run general and specialized lanes. Grok is X/Twitter-only and runs one specialized query per research round; quota exhaustion is reported as `unavailable`, not as a successful answer. Tavily rotates through the private five-Key pool, and Exa runs through the Agent-Reach/mcporter chain. Grok, Gemini, and Qianwen browser runtimes close after 10 idle minutes.

No family Key, provider credential, cookie, profile, or private runtime state is stored in this plugin or public repository.
