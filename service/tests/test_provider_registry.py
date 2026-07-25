from tools.search_provider_registry import provider_ids


def test_runtime_has_eight_sources():
    assert provider_ids() == (
        "doubao", "yuanbao", "wenxin", "tavily", "exa", "gemini", "grok", "qianwen"
    )
