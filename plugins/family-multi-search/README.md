# Family Multi Search ZCode Plugin

This native ZCode plugin bundles:

- stdio MCP server `family-multi-search`;
- Skill `multi-search-remote`;
- a standard-library REST bridge to the authenticated remote search service.

The plugin does not embed a family Key. Configure the Key first with the portable release installer:

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

No family Key, provider credential, cookie, profile, or private runtime state is stored in this plugin or public repository.
