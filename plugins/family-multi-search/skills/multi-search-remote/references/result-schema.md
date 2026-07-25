# Result schema

The service returns structured evidence rather than a final truth guarantee.

Important fields may include:

- `status`: queued, running, complete, partial, failed, or cancelled.
- `request_id`: opaque job identifier owned by one family Key.
- `claims`: candidate conclusions with citation and confidence metadata.
- `unique_citations` / `sources`: normalized source ledger.
- `providers`: per-provider status, retained observations, and citation lists.
- `conflicts`: incompatible dates, scopes, values, or source statements.
- `unknowns` / `verification_queue`: explicit evidence gaps.
- `coverage`: which requested research dimensions have direct evidence.
- `evidence_digest`: deterministic summary of evidence completeness.

A complete transport response does not imply every factual dimension was verified. Preserve provider status and evidence gaps in the user-facing answer.
