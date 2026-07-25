"""Pure normalization and URL-level deduplication for multi-search evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from tools.research_brief import build_coverage_matrix, normalize_research_brief


@dataclass(frozen=True)
class Citation:
    title: str
    url: str | None
    publisher: str | None
    snippet: str | None
    provider: str
    is_direct_url: bool
    is_resolved: bool
    evidence_role: str
    claim_scope: list[str]


_TRACKING_QUERY_PARAMETERS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
_SUCCESSFUL_PROVIDER_STATUSES = {"success", "complete", "ok"}
_PLATFORM_OBSERVATION_SIGNAL_PATTERNS = (
    ("price", re.compile(r"(?:[¥￥]\s*\d|\d+(?:\.\d+)?\s*元|团购价|优惠价|票价|售价|人均)")),
    ("package", re.compile(r"(?:套餐|团购|优惠券?|代金券?|门票|烧烤|露营|住宿|帐篷|\d+\s*人(?:套餐|票|团))")),
    ("traffic", re.compile(r"(?:客流|人次|游客|到店|排队|爆满|满场|一帐难求|日均|每天)")),
    ("revenue", re.compile(r"(?:营业额|营收|流水|收入|回本|ROI|投入回报)", re.IGNORECASE)),
    ("capacity", re.compile(r"(?:容纳|承载|容量|座位|停车位)")),
    (
        "investment",
        re.compile(
            r"(?:融资|估值|投资|financ(?:ing|ed)?|valuation|investment)",
            re.IGNORECASE,
        ),
    ),
    (
        "market",
        re.compile(
            r"(?:市场(?:规模|份额|占有率)?|市占率|CAGR|复合(?:年)?增长率|增长(?:率)?|增速|market(?:\s+size)?|growth|share)",
            re.IGNORECASE,
        ),
    ),
    (
        "project",
        re.compile(
            r"(?:项目|立项|审批|批复|规划|招标|投标|中标|开工|建设|预算|project|approval|planning|tender|construction|budget)",
            re.IGNORECASE,
        ),
    ),
    (
        "policy",
        re.compile(
            r"(?:政策|法规|监管|条例|规定|标准|规范|policy|regulation|standard)",
            re.IGNORECASE,
        ),
    ),
    (
        "competition",
        re.compile(
            r"(?:竞争(?:对手|格局)?|竞品|对标|competitor|competition)",
            re.IGNORECASE,
        ),
    ),
    ("overview", re.compile(r"(?:概览|概况|简介|overview|summary)", re.IGNORECASE)),
    ("risk", re.compile(r"(?:风险|隐患|不确定性|risk|threat)", re.IGNORECASE)),
    ("demand", re.compile(r"(?:需求|客群|消费意愿|demand)", re.IGNORECASE)),
    ("feasibility", re.compile(r"(?:可行性|可实施|feasibility|viability)", re.IGNORECASE)),
    ("budget", re.compile(r"(?:预算|造价|成本测算|budget)", re.IGNORECASE)),
    ("timeline", re.compile(r"(?:工期|时间表|里程碑|timeline|schedule)", re.IGNORECASE)),
    ("stakeholders", re.compile(r"(?:利益相关方|相关部门|股东|stakeholders?)", re.IGNORECASE)),
    ("business_model", re.compile(r"(?:商业模式|盈利模式|business\s+model)", re.IGNORECASE)),
    ("thesis", re.compile(r"(?:投资逻辑|投资论点|核心论点|investment\s+thesis|thesis)", re.IGNORECASE)),
    ("financials", re.compile(r"(?:财务|融资金额|现金流|financials?|cash\s*flow)", re.IGNORECASE)),
    ("valuation", re.compile(r"(?:估值|valuation)", re.IGNORECASE)),
    ("definition", re.compile(r"(?:定义|范围界定|definition)", re.IGNORECASE)),
    ("growth", re.compile(r"(?:增长率|增速|复合增长|growth|CAGR)", re.IGNORECASE)),
    ("drivers", re.compile(r"(?:驱动因素|驱动力|drivers?)", re.IGNORECASE)),
    ("segments", re.compile(r"(?:细分市场|市场细分|segments?)", re.IGNORECASE)),
    ("trends", re.compile(r"(?:趋势|演变|trends?)", re.IGNORECASE)),
    ("positioning", re.compile(r"(?:定位|positioning)", re.IGNORECASE)),
    ("products", re.compile(r"(?:产品|新品|products?)", re.IGNORECASE)),
    ("pricing", re.compile(r"(?:定价|价格|pricing)", re.IGNORECASE)),
    ("channels", re.compile(r"(?:渠道|channels?)", re.IGNORECASE)),
    ("strengths", re.compile(r"(?:优势|长处|strengths?)", re.IGNORECASE)),
    ("weaknesses", re.compile(r"(?:劣势|短板|weaknesses?)", re.IGNORECASE)),
    ("actions", re.compile(r"(?:行动|举措|下一步|actions?)", re.IGNORECASE)),
    ("profit", re.compile(r"(?:利润|毛利|净利|profit|margin)", re.IGNORECASE)),
)
_DIRECT_BUSINESS_VALUE_PATTERN = re.compile(
    r"""
    (?:
        (?P<currency>[¥￥]\s*\d+(?:\.\d+)?)
      | (?P<billion_yuan>\d+(?:\.\d+)?\s*亿\s*元)
      | (?P<hundred_million>\d+(?:\.\d+)?\s*亿(?!\s*元))
      | (?P<percent>\d+(?:\.\d+)?\s*(?:%|％|个百分点))
      | (?P<percent_chinese>百分之\s*\d+(?:\.\d+)?)
      | (?P<yuan>\d+(?:\.\d+)?\s*元)
      | (?P<person_time>\d+(?:\.\d+)?\s*人次)
      | (?P<people>\d+(?:\.\d+)?\s*人(?!\s*年))
      | (?P<units>\d+(?:\.\d+)?\s*(?:位|套|张|折|%))
      | (?P<magnitude>\d+(?:\.\d+)?\s*(?:万|千))
      | (?P<chinese_people>[一二三四五六七八九十]+\s*人(?=(?:次|套餐|票|团|桌|位)))
    )
    """,
    re.VERBOSE,
)
# Temporal tokens can be useful context, but are never a standalone commercial value.
_TEMPORAL_NUMERIC_PATTERN = re.compile(
    r"(?:\d{2,4}\s*年(?:\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*[日号])?)?|\d{1,2}\s*月(?:\s*\d{1,2}\s*[日号])?)"
)
# Retained for consumers of the structured output's historical field name.  Its
# semantics are intentionally stricter than a generic date/count regex: it now
# means a direct, contextually relevant business quantity.
_QUANTIFIED_CONTEXT_PATTERN = _DIRECT_BUSINESS_VALUE_PATTERN
_DIRECT_VALUE_SIGNAL_COMPATIBILITY = {
    "hundred_million": {"investment", "market", "project", "revenue"},
    "percent": {
        "price",
        "package",
        "traffic",
        "revenue",
        "capacity",
        "investment",
        "market",
        "project",
        "policy",
        "competition",
    },
    "percent_chinese": {
        "price",
        "package",
        "traffic",
        "revenue",
        "capacity",
        "investment",
        "market",
        "project",
        "policy",
        "competition",
    },
    "person_time": {"traffic"},
    "people": {"package", "traffic", "capacity"},
    "units": {
        "price",
        "package",
        "traffic",
        "capacity",
        "revenue",
        "investment",
        "market",
        "project",
        "policy",
        "competition",
    },
    "magnitude": {
        "price",
        "package",
        "traffic",
        "revenue",
        "capacity",
        "investment",
        "market",
        "project",
        "policy",
        "competition",
    },
    "chinese_people": {"package", "traffic", "capacity"},
}
_DIRECT_VALUE_PRIORITY = {
    "currency": 5,
    "yuan": 5,
    "billion_yuan": 5,
    "hundred_million": 4,
    "person_time": 4,
    "people": 3,
    "percent": 3,
    "percent_chinese": 3,
    "units": 3,
    "magnitude": 2,
    "chinese_people": 2,
}
_PLATFORM_STATUS_HINTS = (
    "正在搜索中",
    "正在搜索",
    "搜索中",
    "搜索全网",
    "全网搜索",
    "正在思考中",
    "思考中",
    "正在思考",
    "联网搜索中",
    "Searching",
    "Deep Thinking",
    "Thinking",
    "正在编写",
    "深度思考中",
)


def load_profiles(path: Path) -> dict:
    """Load the JSON profile configuration without contacting any provider."""
    with Path(path).open(encoding="utf-8") as profile_file:
        profiles = json.load(profile_file)

    if not isinstance(profiles, dict):
        raise ValueError("evidence profiles must be a JSON object")
    if "profiles" in profiles and not isinstance(profiles["profiles"], dict):
        raise ValueError("evidence profiles field must be a JSON object")
    return profiles


def normalize_citation(
    value: dict | str,
    provider: str,
    profile: dict | None = None,
) -> dict:
    """Convert one provider reference into a JSON-serializable citation record.

    Citation URLs are retained exactly as received.  ``dedupe_key`` is the only
    normalized URL representation and is kept separate from the source URL.
    """
    provider_profile = _citation_profile(profile, provider)
    title, url, publisher, snippet = _citation_values(value)
    is_direct_url = _is_direct_url(url)
    is_resolved = is_direct_url

    citation = Citation(
        title=title,
        url=url,
        publisher=publisher,
        snippet=snippet,
        provider=provider,
        is_direct_url=is_direct_url,
        is_resolved=is_resolved,
        evidence_role=(
            _configured_evidence_role(provider_profile)
            if is_resolved
            else "unresolved_lead"
        ),
        claim_scope=_claim_scope(provider_profile),
    )
    normalized = asdict(citation)
    if isinstance(value, dict):
        for key in ("lane", "source_status", "eligible_for_corroboration"):
            if key in value:
                normalized[key] = value[key]
    citation_text = " ".join(
        item for item in (title, publisher, snippet, url) if isinstance(item, str)
    )
    normalized["signals"] = _platform_observation_signals(citation_text)
    normalized["dedupe_key"] = _dedupe_key(url) if is_resolved else None
    return normalized


def _platform_observations(
    answer: object,
    citations: list[dict],
    source_id: str,
    provider_profile: dict,
    status: str,
    partial: bool,
) -> list[dict]:
    """Retain high-signal platform text as labeled model inputs, not facts."""
    if not bool(provider_profile.get("preserve_platform_observations")):
        return []

    answer_text = answer if isinstance(answer, str) else ""
    if not answer_text.strip():
        return []

    observation_limit = _positive_int(
        provider_profile.get("max_platform_observations"), default=0
    )
    if observation_limit == 0:
        return []

    observation_char_limit = _positive_int(
        provider_profile.get("max_platform_observation_chars"), default=420
    )
    related_url_limit = _positive_int(
        provider_profile.get("max_related_citation_urls"), default=3
    )
    related_citation_urls = _related_citation_urls(citations, related_url_limit)
    model_input_scopes = _model_input_scopes(provider_profile)
    retention_priority = _retention_priority(provider_profile)
    observation_priority = _positive_int(
        provider_profile.get("observation_priority"), default=retention_priority
    )
    candidates: list[tuple[int, dict]] = []
    seen_fragments: set[str] = set()

    for fragment_index, fragment in enumerate(_observation_fragments(answer_text)):
        signals = _platform_observation_signals(fragment)
        if not signals:
            continue
        dedupe_key = re.sub(r"\s+", " ", fragment).strip().lower()
        if dedupe_key in seen_fragments:
            continue
        seen_fragments.add(dedupe_key)
        observation_text, observation_truncated = _signal_centered_excerpt(
            fragment, observation_char_limit
        )
        retained_signals = _platform_observation_signals(observation_text)
        if not retained_signals:
            continue
        retained_direct_business_value = _has_direct_business_value(observation_text)
        candidates.append(
            (
                fragment_index,
                {
                    "provider": source_id,
                    "ecosystem": _text_or_default(provider_profile.get("ecosystem"), "unknown"),
                    "evidence_status": "platform_observed",
                    "source_status": status,
                    "partial": partial,
                    "text": observation_text,
                    "observation_truncated": observation_truncated,
                    "signals": retained_signals,
                    "quantified_context": retained_direct_business_value,
                    "direct_business_value": retained_direct_business_value,
                    "claim_scope": _claim_scope(provider_profile),
                    "model_input_scopes": model_input_scopes,
                    "model_input_eligible": (
                        retained_direct_business_value
                        and bool(retained_signals)
                        and not partial
                        and status.lower() in _SUCCESSFUL_PROVIDER_STATUSES
                        and bool(model_input_scopes)
                    ),
                    "eligible_for_corroboration": False,
                    "related_citation_urls": related_citation_urls,
                    "citation_linkage": "same_provider_round_not_claim_bound",
                    "retention_priority": retention_priority,
                    "observation_priority": observation_priority,
                },
            )
        )

    candidates.sort(
        key=lambda candidate: (
            0 if candidate[1]["quantified_context"] else 1,
            -len(candidate[1]["signals"]),
            candidate[0],
        )
    )
    return [observation for _, observation in candidates[:observation_limit]]


def _observation_fragments(answer: str) -> list[str]:
    cleaned_answer = strip_platform_stream_status(answer)
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in cleaned_answer.splitlines()
    ]
    lines = [line for line in lines if line]
    fragments: list[str] = []
    for index, line in enumerate(lines):
        sentences = [
            sentence.strip()
            for sentence in re.split(r"(?<=[。！？；;])", line)
            if sentence.strip()
        ]
        for sentence in sentences:
            if index > 0 and lines[index - 1].endswith((":", "：")):
                sentence = f"{lines[index - 1]} {sentence}"
            fragments.append(sentence)
    return fragments


def _platform_observation_signals(text: str) -> list[str]:
    return [
        name
        for name, pattern in _PLATFORM_OBSERVATION_SIGNAL_PATTERNS
        if pattern.search(text)
    ]


def has_platform_observation_signal(value: object) -> bool:
    """Return whether text contains a business signal worth retaining in a stream."""
    return isinstance(value, str) and bool(_platform_observation_signals(value))


def strip_platform_stream_status(
    value: object,
    status_hints: tuple[str, ...] | None = None,
) -> str:
    """Remove provider progress labels while preserving any substantive remainder."""
    text = value if isinstance(value, str) else ""
    hints = status_hints or _PLATFORM_STATUS_HINTS
    normalized_hints = sorted(hints, key=len, reverse=True)
    retained: list[str] = []
    for raw_line in text.splitlines() or [text]:
        line = raw_line.strip()
        lower_line = line.lower()
        while line:
            matched_hint = next(
                (
                    hint
                    for hint in normalized_hints
                    if lower_line.startswith(hint.lower())
                ),
                None,
            )
            if matched_hint is None:
                break
            line = line[len(matched_hint):].lstrip(" \t:：-—")
            lower_line = line.lower()
        if not line:
            continue
        if any(hint.lower() in lower_line for hint in normalized_hints) and not (
            has_platform_observation_signal(line)
        ):
            continue
        retained.append(line)
    return "\n".join(retained).strip()


def _related_citation_urls(citations: list[dict], limit: int) -> list[str]:
    urls: list[str] = []
    for citation in citations:
        url = citation.get("url")
        if not citation.get("is_resolved") or not isinstance(url, str) or not url:
            continue
        if url not in urls:
            urls.append(url)
        if len(urls) >= limit:
            break
    return urls


def _has_direct_business_value(text: str) -> bool:
    """Return whether retained text contains a usable non-temporal business value."""
    return any(_direct_business_value_matches(text))


def _direct_business_value_matches(text: str):
    for value_match in _DIRECT_BUSINESS_VALUE_PATTERN.finditer(text):
        if _is_direct_business_value_match(text, value_match):
            yield value_match


def _is_direct_business_value_match(text: str, value_match) -> bool:
    """Keep price values directly; require business context for counts and magnitudes."""
    value_text = value_match.group(0)
    if _TEMPORAL_NUMERIC_PATTERN.fullmatch(value_text):
        return False

    value_kind = value_match.lastgroup
    if value_kind in {"currency", "yuan", "billion_yuan"}:
        return True

    allowed_signals = _DIRECT_VALUE_SIGNAL_COMPATIBILITY.get(value_kind, set())
    if not allowed_signals:
        return False
    surrounding = text[
        max(0, value_match.start() - 80): min(len(text), value_match.end() + 80)
    ]
    return bool(set(_platform_observation_signals(surrounding)) & allowed_signals)


def _direct_value_priority(value_match) -> int:
    return _DIRECT_VALUE_PRIORITY.get(value_match.lastgroup, 0)


def _signal_centered_excerpt(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False

    anchor = _business_value_anchor(text)
    if anchor is None or limit <= 2:
        return text[:limit], True

    window = limit - 2
    start = max(0, anchor.start() - window // 2)
    end = min(len(text), start + window)
    start = max(0, end - window)
    prefix = "…" if start else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}", True


def _business_value_anchor(text: str):
    direct_values = list(_direct_business_value_matches(text))
    if direct_values:
        return max(
            direct_values,
            key=lambda value_match: (
                _direct_value_priority(value_match),
                -value_match.start(),
            ),
        )
    return None


def _positive_int(value: object, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return default


def _model_input_scopes(profile: dict) -> list[str]:
    scopes = profile.get("model_input_scopes", [])
    if isinstance(scopes, str):
        return [scopes]
    if not isinstance(scopes, list):
        return []
    return [scope for scope in scopes if isinstance(scope, str)]


def build_research_round(
    query: str,
    profile_name: str,
    profiles: dict,
    elapsed_ms: int,
    results: dict[str, dict],
    research_brief: dict | None = None,
) -> dict:
    """Build an offline, JSON-serializable evidence round from provider results.

    A duplicate group records delivery overlap only.  It deliberately does not
    calculate a confidence or consensus score from the number of providers.
    """
    profile = _select_profile(profiles, profile_name)
    normalized_brief = normalize_research_brief(research_brief, profile_name)
    provider_records: dict[str, dict] = {}
    decision_inputs: list[dict] = []
    grouped_by_dedupe_key: dict[str, list[tuple[int, str, dict, dict]]] = {}
    unresolved_citations: list[tuple[int, dict]] = []
    citation_index = 0
    observation_index = 0

    for requested_source_id, raw_result in results.items():
        source_id, provider_profile = _resolve_provider_profile(
            profile, requested_source_id
        )
        raw_record = raw_result if isinstance(raw_result, dict) else {}
        partial = bool(raw_record.get("partial"))
        supplied_status = raw_record.get("status")
        status = (
            supplied_status
            if isinstance(supplied_status, str) and supplied_status
            else "success"
        )
        if partial or status.lower() == "partial":
            partial = True
            status = "partial"

        citations = [
            normalize_citation(reference, source_id, provider_profile)
            for reference in _references_from(raw_record)
            if isinstance(reference, (dict, str))
        ]
        provider_record = {
            "source_id": source_id,
            "ecosystem": _text_or_default(provider_profile.get("ecosystem"), "unknown"),
            "status": status,
            "partial": partial,
            "eligible_for_corroboration": (
                not partial and status.lower() in _SUCCESSFUL_PROVIDER_STATUSES
            ),
            "citations": citations,
            "retention_priority": _retention_priority(provider_profile),
            "preserve_references": bool(provider_profile.get("preserve_references")),
        }
        observations = _platform_observations(
            raw_record.get("answer"),
            citations,
            source_id,
            provider_profile,
            status,
            partial,
        )
        provider_record["platform_observation_count"] = len(observations)
        provider_records[source_id] = provider_record
        for observation in observations:
            observation["_order"] = observation_index
            observation_index += 1
            decision_inputs.append(observation)

        for citation in citations:
            citation["_order"] = citation_index
            citation["source_order"] = citation_index
            citation_index += 1
            dedupe_key = citation["dedupe_key"]
            if dedupe_key is None:
                citation["is_unique"] = False
                unresolved_citations.append((citation["_order"], citation))
                continue
            grouped_by_dedupe_key.setdefault(dedupe_key, []).append(
                (citation["_order"], source_id, citation, provider_record)
            )

    unique_entries: list[tuple[int, dict]] = []
    coverage_entries: list[tuple[int, dict]] = []
    duplicate_groups: list[dict] = []
    for dedupe_key, entries in grouped_by_dedupe_key.items():
        is_unique = len(entries) == 1
        for _, _, citation, _ in entries:
            citation["is_unique"] = is_unique

        representative = _select_representative(entries)
        first_index = entries[0][0]
        unique_entries.append((first_index, _public_citation(representative[2])))

        eligible_entries = [
            entry
            for entry in entries
            if bool(
                entry[2].get(
                    "eligible_for_corroboration",
                    entry[3]["eligible_for_corroboration"],
                )
            )
        ]
        coverage_representative = _select_representative(
            eligible_entries or entries
        )
        coverage_citation = {
            **_public_citation(coverage_representative[2]),
            "eligible_for_corroboration": bool(
                coverage_representative[2].get(
                    "eligible_for_corroboration",
                    coverage_representative[3]["eligible_for_corroboration"],
                )
            ),
        }
        coverage_entries.append((first_index, coverage_citation))

        if not is_unique:
            duplicate_groups.append(
                {
                    "dedupe_key": dedupe_key,
                    "url": representative[2]["url"],
                    "providers": _provider_ids(entries),
                }
            )

    for index, citation in unresolved_citations:
        public_citation = _public_citation(citation)
        unique_entries.append((index, public_citation))
        provider_record = provider_records.get(citation["provider"], {})
        coverage_entries.append(
            (
                index,
                {
                    **public_citation,
                    "eligible_for_corroboration": bool(
                        citation.get(
                            "eligible_for_corroboration",
                            provider_record.get("eligible_for_corroboration"),
                        )
                    ),
                },
            )
        )

    unique_entries.sort(key=lambda entry: entry[0])
    coverage_entries.sort(key=lambda entry: entry[0])

    for provider_record in provider_records.values():
        provider_record["citations"] = [
            _public_citation(citation) for citation in provider_record["citations"]
        ]

    decision_inputs.sort(
        key=lambda observation: (
            -_positive_int(observation.get("observation_priority"), default=0),
            -_positive_int(observation.get("retention_priority"), default=0),
            _positive_int(observation.get("_order"), default=0),
        )
    )
    public_decision_inputs = [
        _public_observation(observation) for observation in decision_inputs
    ]
    coverage_matrix = build_coverage_matrix(
        normalized_brief,
        [citation for _, citation in coverage_entries],
        public_decision_inputs,
    )

    return {
        "query": query,
        "profile": profile_name,
        "elapsed_ms": elapsed_ms,
        "research_brief": normalized_brief,
        "coverage_matrix": coverage_matrix,
        "decision_inputs": public_decision_inputs,
        "providers": provider_records,
        "unique_citations": [citation for _, citation in unique_entries],
        "duplicate_groups": duplicate_groups,
        "verification_queue": [],
    }


def _citation_values(value: dict | str) -> tuple[str, str | None, str | None, str | None]:
    if isinstance(value, str):
        direct_url = value if _is_direct_url(value) else None
        return value, direct_url, None, None

    url = _first_text(value, ("url", "link", "href", "source_url", "reference_url"))
    title = _first_text(value, ("title", "name", "label", "text", "reference"))
    if title is None:
        title = url or ""
    publisher = _first_text(value, ("publisher", "source", "site_name", "site"))
    snippet = _first_text(value, ("snippet", "content", "description", "summary"))
    return title, url, publisher, snippet


def _first_text(value: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _is_direct_url(url: str | None) -> bool:
    if not isinstance(url, str) or not url:
        return False
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def _dedupe_key(url: str | None) -> str | None:
    """Build a conservative source-identity key without changing ``url``."""
    if not _is_direct_url(url):
        return None
    assert url is not None
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    if (scheme == "http" and netloc.endswith(":80")) or (
        scheme == "https" and netloc.endswith(":443")
    ):
        netloc = netloc.rsplit(":", 1)[0]

    query = urlencode(
        [
            (name, item)
            for name, item in parse_qsl(parsed.query, keep_blank_values=True)
            if not name.lower().startswith("utm_")
            and name.lower() not in _TRACKING_QUERY_PARAMETERS
        ],
        doseq=True,
    )
    return urlunsplit((scheme, netloc, parsed.path or "/", query, ""))


def _select_profile(profiles: dict, profile_name: str) -> dict:
    profile_map = profiles.get("profiles", profiles)
    if not isinstance(profile_map, dict):
        raise ValueError("profiles must be a mapping")
    profile = profile_map.get(profile_name)
    if not isinstance(profile, dict):
        raise ValueError(f"unknown evidence profile: {profile_name}")
    return profile


def _resolve_provider_profile(profile: dict, requested_source_id: str) -> tuple[str, dict]:
    providers = profile.get("providers")
    if not isinstance(providers, dict):
        return requested_source_id, {}

    requested_lower = requested_source_id.lower()
    for configured_source_id, candidate in providers.items():
        if not isinstance(candidate, dict):
            continue
        aliases = candidate.get("aliases", [])
        names = [configured_source_id, candidate.get("source_id"), *aliases]
        if any(
            isinstance(name, str) and name.lower() == requested_lower
            for name in names
        ):
            source_id = _text_or_default(candidate.get("source_id"), configured_source_id)
            return source_id, candidate
    return requested_source_id, {}


def _citation_profile(profile: dict | None, provider: str) -> dict:
    if not isinstance(profile, dict):
        return {}
    providers = profile.get("providers")
    if isinstance(providers, dict):
        _, provider_profile = _resolve_provider_profile(profile, provider)
        return provider_profile
    return profile


def _claim_scope(profile: dict) -> list[str]:
    scope = profile.get("claim_scope", profile.get("scope", []))
    if isinstance(scope, str):
        return [scope]
    if not isinstance(scope, list):
        return []
    return [item for item in scope if isinstance(item, str)]


def _configured_evidence_role(profile: dict) -> str:
    role = profile.get("evidence_role")
    return role if isinstance(role, str) and role else "direct_source_evidence"


def _references_from(result: dict) -> list[Any]:
    for key in ("citations", "references", "results", "sources"):
        if key not in result:
            continue
        references = result[key]
        if isinstance(references, list):
            return references
        if isinstance(references, (dict, str)):
            return [references]
        return []
    return []


def _retention_priority(profile: dict) -> int:
    priority = profile.get("retention_priority", 0)
    return priority if isinstance(priority, int) and not isinstance(priority, bool) else 0


def _select_representative(
    entries: list[tuple[int, str, dict, dict]],
) -> tuple[int, str, dict, dict]:
    # Priority controls which retained copy is easiest to review, not confidence.
    return max(entries, key=lambda entry: (_retention_priority(entry[3]), -entry[0]))


def _provider_ids(entries: list[tuple[int, str, dict, dict]]) -> list[str]:
    provider_ids: list[str] = []
    for _, source_id, _, _ in entries:
        if source_id not in provider_ids:
            provider_ids.append(source_id)
    return provider_ids


def _public_citation(citation: dict) -> dict:
    return {key: value for key, value in citation.items() if key != "_order"}


def _public_observation(observation: dict) -> dict:
    return {key: value for key, value in observation.items() if key != "_order"}


def _text_or_default(value: Any, default: str) -> str:
    return value if isinstance(value, str) and value else default
