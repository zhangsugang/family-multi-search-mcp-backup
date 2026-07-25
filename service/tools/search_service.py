from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SearchService:
    """Transport-neutral facade over the existing eight-source runtime."""

    runtime: Any

    async def research_round(
        self,
        *,
        query: str,
        profile: str = "general",
        research_brief: dict | None = None,
        max_answer: int = 2200,
        max_refs: int = 30,
        timeout: int = 90,
        mode: str = "balanced",
    ) -> dict:
        if mode != "balanced":
            raise ValueError("mode currently supports only 'balanced'")
        return await self.runtime._run_research_round(
            query=query,
            profile=profile,
            research_brief=research_brief,
            max_answer=max_answer,
            max_refs=max_refs,
            timeout=timeout,
        )

    async def legacy_search_all(
        self,
        *,
        query: str,
        max_answer: int = 2200,
        max_refs: int = 10,
        timeout: int = 90,
        profile: str = "general",
    ) -> str:
        research = await self.research_round(
            query=query,
            profile=profile,
            max_answer=max_answer,
            max_refs=max_refs,
            timeout=timeout,
        )
        return self.runtime._render_legacy_search_all(research, max_refs)

    def provider_status(self) -> dict:
        config = self.runtime.get_runtime_config()
        return {
            "status": "ready",
            "provider_count": len(self.runtime.provider_ids()),
            "providers": list(self.runtime.provider_ids()),
            "configured": {
                "tavily": bool(config.get("tavily_api_keys")),
                "exa": bool(self.runtime.shutil.which("mcporter")),
                "yuanbao": self.runtime._yuanbao_state_path().exists(),
                "wenxin": self.runtime._wenxin_state_path().exists(),
                "gemini": self.runtime._gemini_profile_path().exists(),
                "grok": self.runtime._grok_profile_path().exists(),
                "qianwen": self.runtime._qianwen_profile_path().exists(),
            },
        }
