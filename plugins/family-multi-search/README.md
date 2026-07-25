# Family Multi Search ZCode Plugin

This plugin bundles:

- remote MCP server `family-multi-search`;
- Skill `multi-search-remote`;
- sensitive install-time family Key configuration.

Install from the public marketplace repository:

```bash
claude plugin marketplace add zhangsugang/family-multi-search-mcp-backup
claude plugin install family-multi-search@family-multi-search \
  --config family_key='YOUR_FAMILY_KEY'
```

Restart ZCode or start a new session after installation.

To enable updates, open `/plugin`, select **Marketplaces**, select `family-multi-search`, and enable auto-update. ZCode checks the GitHub marketplace at startup; use `/reload-plugins` when prompted.

No family Key, provider credential, cookie, profile, or private runtime state is stored in this plugin.
