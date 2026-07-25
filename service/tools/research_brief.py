"""Offline normalization and evidence-coverage helpers for research reports."""

from __future__ import annotations

from collections.abc import Mapping


DEFAULT_DIMENSIONS = {
    "general": ["overview", "evidence", "risks"],
    "project_feasibility": [
        "demand",
        "feasibility",
        "budget",
        "timeline",
        "stakeholders",
        "risks",
    ],
    "investment_due_diligence": [
        "thesis",
        "market",
        "business_model",
        "financials",
        "valuation",
        "risks",
    ],
    "industry_landscape": [
        "definition",
        "market",
        "growth",
        "drivers",
        "policy",
        "segments",
        "competition",
        "trends",
    ],
    "competitor_analysis": [
        "positioning",
        "products",
        "pricing",
        "channels",
        "strengths",
        "weaknesses",
        "actions",
    ],
    "operating_analysis": [
        "demand",
        "pricing",
        "traffic",
        "capacity",
        "revenue",
        "profit",
        "risks",
    ],
}


_COVERAGE_SIGNAL_DIMENSIONS = {
    "general": {
        "overview": {"overview"},
        "risk": {"risks"},
    },
    "project_feasibility": {
        "demand": {"demand"},
        "feasibility": {"feasibility"},
        "budget": {"budget"},
        "timeline": {"timeline"},
        "stakeholders": {"stakeholders"},
        "risk": {"risks"},
    },
    "investment_due_diligence": {
        "thesis": {"thesis"},
        "market": {"market"},
        "business_model": {"business_model"},
        "financials": {"financials"},
        "valuation": {"valuation"},
        "risk": {"risks"},
    },
    "industry_landscape": {
        "definition": {"definition"},
        "market": {"market"},
        "growth": {"growth"},
        "drivers": {"drivers"},
        "policy": {"policy"},
        "segments": {"segments"},
        "competition": {"competition"},
        "trends": {"trends"},
    },
    "competitor_analysis": {
        "positioning": {"positioning"},
        "products": {"products"},
        "pricing": {"pricing"},
        "channels": {"channels"},
        "strengths": {"strengths"},
        "weaknesses": {"weaknesses"},
        "actions": {"actions"},
    },
    "operating_analysis": {
        "overview": {"基础信息"},
        "demand": {"demand", "客流热度"},
        "pricing": {"pricing", "团购活动"},
        "package": {"pricing", "团购活动"},
        "traffic": {"traffic", "客流热度"},
        "capacity": {"capacity", "地址交通"},
        "revenue": {"revenue", "投资运营"},
        "profit": {"profit", "投资运营"},
        "investment": {"投资运营"},
        "project": {"基础信息", "投资运营", "运营主体"},
        "stakeholders": {"运营主体", "法人", "实际控制人"},
        "business_model": {"投资运营", "招商业态"},
        "policy": {"基础信息", "风险争议"},
        "risk": {"risks", "风险争议"},
    },
}
_COVERAGE_SCOPE_DIMENSIONS = {
    "operating_analysis": {
        "coupon": {"pricing", "团购活动"},
        "local_life": {
            "demand", "pricing", "traffic", "基础信息", "地址交通", "旅游攻略",
            "团购活动", "客流热度",
        },
        "merchant_campaign": {"demand", "pricing", "团购活动", "客流热度"},
        "short_video": {"traffic", "旅游攻略", "客流热度"},
        "official_account": {
            "demand", "pricing", "risks", "基础信息", "开放时间", "团购活动", "风险争议",
        },
        "video_account": {"demand", "traffic", "旅游攻略", "客流热度"},
        "local_context": {
            "demand", "risks", "基础信息", "地址交通", "开放时间", "旅游攻略", "风险争议",
        },
        "company_registry": {"运营主体", "法人", "实际控制人", "招商业态"},
    },
}


_PROFILE_RESEARCH_KINDS = {
    "china_local_business": "operating_analysis",
    "project_feasibility": "project_feasibility",
    "investment_due_diligence": "investment_due_diligence",
    "industry_landscape": "industry_landscape",
    "competitor_analysis": "competitor_analysis",
    "operating_analysis": "operating_analysis",
}
_SUPPORTED_DEPTHS = {"standard", "deep"}
_SUPPORTED_REPORT_MODES = {"full"}
_MISSING = object()
_PLACE_HINTS = (
    "园", "景区", "古镇", "街区", "公园", "文创", "商场", "广场", "项目",
    "度假区", "酒店", "民宿", "村", "镇", "乐园", "博物馆",
)
_PLACE_DIMENSIONS = (
    "基础信息", "地址交通", "开放时间", "旅游攻略", "团购活动", "客流热度",
    "投资运营", "运营主体", "法人", "实际控制人", "招商业态", "风险争议",
)


def build_research_brief(query: str, depth: str = "deep") -> dict:
    """Infer a bounded offline research brief without sending the query elsewhere."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    if depth not in _SUPPORTED_DEPTHS:
        raise ValueError(f"unknown depth: {depth!r}")

    entity = query.strip()
    place_like = any(hint in entity for hint in _PLACE_HINTS)
    if place_like:
        required_count = 6 if depth == "deep" else 4
        required = list(_PLACE_DIMENSIONS[:required_count])
        optional = list(_PLACE_DIMENSIONS[required_count:required_count + 8])
        research_kind = "operating_analysis"
    else:
        required = list(DEFAULT_DIMENSIONS["general"])
        optional = []
        research_kind = "general"

    return {
        "query": entity,
        "research_kind": research_kind,
        "entities": [entity],
        "geography": "not specified",
        "timeframe": "current with relevant history",
        "decision_question": entity,
        "required_dimensions": required,
        "optional_dimensions": optional,
        "depth": depth,
        "report_mode": "full",
    }


def normalize_research_brief(value: object, profile_name: str) -> dict:
    """Return a normalized, offline research brief for a selected profile."""
    if value is None:
        supplied: Mapping = {}
    elif isinstance(value, Mapping):
        supplied = value
    else:
        raise ValueError("research brief must be a mapping or None")

    research_kind = _research_kind(supplied, profile_name)
    depth = _enum_value(supplied, "depth", "standard", _SUPPORTED_DEPTHS)
    report_mode = _enum_value(supplied, "report_mode", "full", _SUPPORTED_REPORT_MODES)
    required_dimensions = _required_dimensions(supplied, research_kind)
    optional_dimensions = _trimmed_strings(supplied.get("optional_dimensions", []))[:8]

    return {
        "query": _text_or_default(supplied.get("query"), ""),
        "research_kind": research_kind,
        "entities": _trimmed_strings(supplied.get("entities", [])),
        "geography": _text_or_default(supplied.get("geography"), "not specified"),
        "timeframe": _text_or_default(supplied.get("timeframe"), "not specified"),
        "decision_question": _text_or_default(supplied.get("decision_question"), ""),
        "required_dimensions": required_dimensions,
        "optional_dimensions": optional_dimensions,
        "depth": depth,
        "report_mode": report_mode,
    }


def build_coverage_matrix(
    brief: dict,
    citations: list[dict],
    observations: list[dict],
) -> list[dict]:
    """Summarize direct, platform-observed, and unresolved coverage by dimension."""
    if isinstance(brief, Mapping):
        requested_dimensions = list(brief.get("required_dimensions", []))
        requested_dimensions.extend(brief.get("optional_dimensions", []))
    else:
        requested_dimensions = []
    dimensions = list(dict.fromkeys(_trimmed_strings(requested_dimensions)))
    citation_records = _mappings(citations)
    observation_records = _mappings(observations)
    research_kind = _text_or_default(
        brief.get("research_kind") if isinstance(brief, Mapping) else None,
        "general",
    )
    citation_dimensions = [
        _evidence_dimensions(record, research_kind, dimensions, is_citation=True)
        for record in citation_records
    ]
    observation_dimensions = [
        _evidence_dimensions(record, research_kind, dimensions, is_citation=False)
        for record in observation_records
    ]
    matrix: list[dict] = []

    for dimension in dimensions:
        direct_evidence_count = 0
        unresolved_lead_count = 0
        platform_observation_count = 0

        for citation, covered_dimensions in zip(
            citation_records, citation_dimensions, strict=True
        ):
            if dimension not in covered_dimensions:
                continue
            if bool(citation.get("is_resolved")) and bool(
                citation.get("eligible_for_corroboration")
            ):
                direct_evidence_count += 1
            elif not bool(citation.get("is_resolved")):
                unresolved_lead_count += 1

        for observation, covered_dimensions in zip(
            observation_records, observation_dimensions, strict=True
        ):
            if (
                observation.get("evidence_status") == "platform_observed"
                and dimension in covered_dimensions
            ):
                platform_observation_count += 1

        if direct_evidence_count:
            status = "covered"
            follow_up_gap = None
        elif platform_observation_count or unresolved_lead_count:
            status = "partial"
            follow_up_gap = None
        else:
            status = "gap"
            follow_up_gap = (
                f"Find resolved, corroboration-eligible evidence for {dimension}."
            )

        matrix.append(
            {
                "dimension": dimension,
                "status": status,
                "direct_evidence_count": direct_evidence_count,
                "platform_observation_count": platform_observation_count,
                "unresolved_lead_count": unresolved_lead_count,
                "follow_up_gap": follow_up_gap,
            }
        )

    return matrix


def _evidence_dimensions(
    record: Mapping,
    research_kind: str,
    required_dimensions: list[str],
    *,
    is_citation: bool,
) -> set[str]:
    """Map explicit record signals and provider scopes to research dimensions."""
    required = set(required_dimensions)
    signal_map = _COVERAGE_SIGNAL_DIMENSIONS.get(research_kind, {})
    scope_map = _COVERAGE_SCOPE_DIMENSIONS.get(research_kind, {})

    signal_dimensions: set[str] = set()
    for signal in _trimmed_strings(record.get("signals", [])):
        signal_dimensions.update(signal_map.get(signal, set()))

    scope_dimensions: set[str] = set()
    for scope in _claim_scope(record):
        if scope in required:
            scope_dimensions.add(scope)
        scope_dimensions.update(scope_map.get(scope, set()))

    # A resolved or unresolved citation is itself evidence for a general brief,
    # but it does not automatically establish overview or risk findings.
    if is_citation and research_kind == "general" and "evidence" in required:
        signal_dimensions.add("evidence")

    if signal_dimensions and scope_dimensions:
        return required & signal_dimensions & scope_dimensions
    if signal_dimensions:
        return required & signal_dimensions
    # Preserve the helper's narrow explicit-scope contract while refusing to
    # treat a provider's broad capability list as evidence for every dimension.
    if len(scope_dimensions) == 1:
        return required & scope_dimensions
    return set()


def _research_kind(supplied: Mapping, profile_name: str) -> str:
    value = supplied.get("research_kind", _MISSING)
    if value is _MISSING:
        profile_key = profile_name.strip() if isinstance(profile_name, str) else ""
        return _PROFILE_RESEARCH_KINDS.get(profile_key, "general")
    if not isinstance(value, str) or value.strip() not in DEFAULT_DIMENSIONS:
        raise ValueError(f"unknown research_kind: {value!r}")
    return value.strip()


def _enum_value(
    supplied: Mapping,
    field: str,
    default: str,
    allowed: set[str],
) -> str:
    value = supplied.get(field, default)
    if not isinstance(value, str) or value.strip() not in allowed:
        raise ValueError(f"unknown {field}: {value!r}")
    return value.strip()


def _required_dimensions(supplied: Mapping, research_kind: str) -> list[str]:
    if "required_dimensions" not in supplied:
        return list(DEFAULT_DIMENSIONS[research_kind])
    return _trimmed_strings(supplied.get("required_dimensions"))


def _trimmed_strings(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _text_or_default(value: object, default: str) -> str:
    if not isinstance(value, str):
        return default
    return value.strip() or default


def _mappings(value: object) -> list[Mapping]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _claim_scope(value: Mapping) -> list[str]:
    scope = value.get("claim_scope", [])
    if isinstance(scope, str):
        return [scope.strip()] if scope.strip() else []
    return _trimmed_strings(scope)
