---
name: multi-search-remote
description: Use the family eight-source remote search service for current web research, travel/place investigations, company/operator/owner research, investment and risk checks, social/video signals, group-buying and activity discovery, or any question requiring citations, conflicts, unknowns, and confidence explanations.
---

# Multi Search Remote

Use this Skill when the answer depends on current external information or requires evidence from Chinese and global sources.

## Workflow

1. Preserve the user's original target and explicit constraints.
2. For a focused lookup, call MCP `search_once`.
3. For broad research, call MCP `research` and retain its `request_id`.
4. If the job is `queued` or `running`, poll `get_research_result`; do not start duplicate research for the same user.
5. Use `continue_research` only when evidence gaps or the user request justify another round.
6. Preserve Claims, Sources/Citations, Conflicts, Unknowns, provider failures, and confidence explanations in the final answer.

One family Key may bind up to 10 public IP addresses. Research admission, job ownership, and the one-unfinished-job rule are isolated per bound address rather than across the whole shared Key.

When MCP is unavailable in a Skills-only client, invoke the bundled `scripts/remote_search.py` adjacent to this Skill. Typical installed locations are:

```bash
python3 ~/.zcode/skills/multi-search-remote/scripts/remote_search.py research --query "<question>" --wait
python3 ~/.workbuddy/skills/multi-search-remote/scripts/remote_search.py research --query "<question>" --wait
```

## Evidence rules

- Treat provider-generated prose as a lead or platform observation unless a direct source supports it.
- Do not convert a missing citation into a verified fact.
- Explain partial provider failures and evidence gaps.
- Prefer official primary sources for legal entity, ownership, policy, judicial, financial, and safety claims.
- Keep dates, measurement scope, and provenance attached to numerical claims.

## Qianwen boundary

Qianwen is public-information, read-only discovery. Never use it to order, book, pay, transfer, recharge, refund, change accounts, redeem coupons, or retrieve any person's private official records.

See `references/result-schema.md`, `references/qianwen-read-only-policy.md`, and `references/troubleshooting.md` when needed.
