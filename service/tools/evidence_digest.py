from __future__ import annotations

import re
from collections import defaultdict

_NUMBER_WITH_UNIT = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>元|万元|亿元|人次|人|%|平方米|㎡)"
)


def _provider_summary(providers: dict) -> list[dict]:
    summary = []
    for source_id, provider in providers.items():
        status = provider.get("source_status", provider.get("status", "failed"))
        summary.append({
            "source_id": source_id,
            "status": status,
            "partial": bool(provider.get("partial")),
            "citation_count": len(provider.get("citations", [])),
            "platform_observation_count": provider.get(
                "platform_observation_count", 0
            ),
            "eligible_for_corroboration": bool(
                provider.get("eligible_for_corroboration")
            ),
        })
    return summary


def _conflicts(observations: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for observation in observations:
        text = observation.get("text", "")
        signals = observation.get("signals", [])
        for match in _NUMBER_WITH_UNIT.finditer(text):
            for signal in signals:
                grouped[(signal, match.group("unit"))].append({
                    "provider": observation.get("provider"),
                    "text": text,
                    "value": match.group("value"),
                    "unit": match.group("unit"),
                    "evidence_status": observation.get("evidence_status"),
                })
    conflicts = []
    for (signal, unit), records in sorted(grouped.items()):
        if len({record["value"] for record in records}) > 1:
            records.sort(key=lambda record: (
                record["provider"] or "",
                record["value"],
                record["text"],
                record["evidence_status"] or "",
            ))
            conflicts.append({
                "signal": signal,
                "unit": unit,
                "records": records,
                "resolution": "manual_review_required",
            })
    return conflicts


def build_evidence_digest(research: dict) -> dict:
    coverage = research.get("coverage_matrix", [])
    observations = research.get("decision_inputs", [])
    return {
        "provider_summary": _provider_summary(research.get("providers", {})),
        "direct_evidence": list(research.get("unique_citations", [])),
        "platform_observations": list(observations),
        "coverage_gaps": [
            item for item in coverage if item.get("status") in {"partial", "gap"}
        ],
        "conflicts_to_review": _conflicts(observations),
        "unresolved_leads": [
            item for item in research.get("verification_queue", [])
            if item.get("kind") == "unresolved_citation"
        ],
        "duplicate_documents": list(research.get("duplicate_groups", [])),
    }
