---
name: multi-search-remote
description: Use the family eight-source remote search service for current web research, travel/place investigations, company/operator/owner research, investment and risk checks, social/video signals, group-buying and activity discovery, or any question requiring citations, conflicts, unknowns, and confidence explanations.
---

# Multi Search Remote

Use this Skill when the answer depends on current external information or when the user asks for deep research across Chinese and global sources.

## Workflow

1. Preserve the user's original target and explicit constraints.
2. For a focused lookup, call the remote MCP `search_once` tool when available. In Skills-only clients run:
   ```bash
   python3 ~/.zcode/skills/multi-search-remote/scripts/remote_search.py search --query "<question>"
   ```
3. For broad research, use MCP `research`, or the REST wrapper:
   ```bash
   python3 ~/.zcode/skills/multi-search-remote/scripts/remote_search.py research --query "<question>" --wait
   ```
4. If the service returns a running job, retrieve it with `get_research_result` or `remote_search.py get <request_id>`.
5. Continue only when the returned evidence gaps or the user request justify another round.
6. In the final answer preserve Claims, Sources/Citations, Conflicts, Unknowns, provider failures, and confidence explanations.

## Evidence rules

- Treat provider-generated prose as a lead or platform observation unless a direct source supports it.
- Do not convert a missing citation into a verified fact.
- Explain partial provider failures and evidence gaps.
- Prefer official primary sources for legal entity, ownership, policy, judicial, financial, and safety claims.
- Keep dates, measurement scope, and provenance attached to numerical claims.

## Qianwen boundary

Qianwen is public-information, read-only discovery. Never use it to order, book, pay, transfer, recharge, refund, change accounts, redeem coupons, or retrieve any person's private official records.

See `references/result-schema.md`, `references/qianwen-read-only-policy.md`, and `references/troubleshooting.md` when needed.
