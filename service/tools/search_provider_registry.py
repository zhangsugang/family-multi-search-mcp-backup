from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    source_id: str
    display_name: str
    result_kind: str
    timeout_seconds: int
    progress_enabled: bool
    community_source: bool
    requires_private_state: bool


PROVIDER_SPECS = (
    ProviderSpec("doubao", "豆包", "ai_platform", 82, True, True, True),
    ProviderSpec("yuanbao", "元宝", "ai_platform", 82, True, True, True),
    ProviderSpec("wenxin", "文心", "ai_platform", 82, True, False, False),
    ProviderSpec("tavily", "Tavily", "structured_web", 25, False, False, True),
    ProviderSpec("exa", "Exa", "semantic_web", 55, False, False, False),
    ProviderSpec("gemini", "Gemini / YouTube", "ai_platform", 82, True, True, True),
    ProviderSpec("grok", "Grok / X", "ai_platform", 82, True, True, True),
    ProviderSpec("qianwen", "千问 / 阿里生态", "ai_platform", 82, True, True, True),
)

PROVIDER_NAMES = {spec.source_id: spec.display_name for spec in PROVIDER_SPECS}
AI_PROVIDER_IDS = {
    spec.source_id for spec in PROVIDER_SPECS if spec.result_kind == "ai_platform"
}


def provider_ids() -> tuple[str, ...]:
    return tuple(spec.source_id for spec in PROVIDER_SPECS)
