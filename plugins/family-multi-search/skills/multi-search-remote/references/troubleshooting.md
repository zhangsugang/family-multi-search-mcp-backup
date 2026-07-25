# Troubleshooting

## `401` authorization failure

Run `setup.sh` again with the assigned family Key, or reconfigure the plugin through `/plugin`. Each family member should use a separate Key. Never paste a Key into GitHub issues or logs.

## ZCode plugin is missing

Check:

```bash
claude plugin marketplace list
claude plugin list
```

Install again if needed:

```bash
claude plugin marketplace add zhangsugang/family-multi-search-mcp-backup
claude plugin install family-multi-search@family-multi-search \
  --config family_key='YOUR_FAMILY_KEY'
```

Restart ZCode or run `/reload-plugins` after installation.

## Enable updates

Open `/plugin`, select **Marketplaces**, choose `family-multi-search`, and enable auto-update. Manual fallback:

```bash
claude plugin marketplace update family-multi-search
claude plugin update family-multi-search@family-multi-search
```

Restart or run `/reload-plugins` when prompted.

## `429` submission rejection

The Key already owns unfinished research or the bounded family queue is full. Retrieve the existing `request_id` instead of starting a duplicate task.

## `queued` result

The request was accepted and is waiting for one of two complete-research workers. Poll `get_research_result`; queued jobs do not use the old 30-second semaphore timeout.

## `partial` result

Some providers timed out, required login, or returned incomplete evidence. Use successful citations and retain listed unknowns rather than filling gaps from memory.

## WorkBuddy

Install with:

```bash
./setup.sh --client workbuddy
```

WorkBuddy uses the bundled `scripts/remote_search.py` over HTTPS REST and does not require ZCode plugin support.
