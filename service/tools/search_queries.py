from __future__ import annotations

from collections.abc import Iterable

try:
    from tools.research_brief import normalize_research_brief
except ModuleNotFoundError:
    from research_brief import normalize_research_brief


_SOURCE_SUFFIXES = {
    "doubao": (
        "重点搜索抖音、头条及可发现的国内社区内容；优先给出直接平台页。"
        "严格区分播放量、到店客流、订单、团购核销和营业额。"
    ),
    "yuanbao": (
        "重点搜索微信公众号、视频号和微博中的官方活动、招商、项目变化及经营信息；"
        "优先给出直接文章或可识别来源卡片。"
    ),
    "wenxin": "重点搜索百度中文网页中的政府、本地媒体、公告、招商和历史原文。",
    "tavily": "重点返回可直接访问的官方网页、独立报道、公告和结构化网页结果。",
    "exa": "重点语义搜索官方原文、深层报道、历史披露和权威材料。",
    "gemini": (
        "进行严格的YouTube社区定向搜索；只把直接视频作为社区来源，列出标题、频道、"
        "发布日期、摘要和YouTube直接视频链接。不得把播放量当作游客、销量或订单；"
        "没有直接视频时明确说明覆盖不足。"
    ),
    "grok": (
        "进行严格的X社区定向搜索；只把公开原帖作为社区来源，列出账号、发布日期、"
        "摘要和x.com/status直接链接。不得用普通网页替代原帖，也不得把浏览量或点赞量"
        "当作游客、销量或订单；没有直接原帖时明确说明覆盖不足。"
    ),
    "qianwen": (
        "同时检索千问可访问的公开阿里生态信息：淘宝商品与价格线索、飞猪机票酒店与旅游产品、"
        "高德地址路线与周边生活服务、饿了么餐饮与本地生活。只做搜索和信息整理；不得下单、"
        "不得预订、不得支付、不得查询个人支付宝、公积金、社保或其他个人政务数据。提供可检查来源。"
    ),
}


_GENERAL_SUFFIX = (
    "进行广泛公开信息搜索，优先官方原文、独立报道、发布日期、数据口径和可直接检查的来源；"
    "不要把平台热度、播放量或点赞量直接推断为客流、订单、收入或投资价值。"
)


def _query_core(query: str, brief: dict, dimensions: list[str]) -> str:
    entities = "、".join(brief["entities"]) or "未指定实体"
    dimension_text = "、".join(dimensions) or "基础信息"
    return (
        f"研究问题：{query.strip()}\n"
        f"研究类型：{brief['research_kind']}；实体：{entities}；地区：{brief['geography']}；"
        f"时间范围：{brief['timeframe']}；研究维度：{dimension_text}。\n"
        "保留发布日期、数据口径和直接来源；不能确认的内容明确标记为线索或估算。"
    )


def build_provider_query_lanes(
    research_brief: dict,
    source_ids: Iterable[str] | None = None,
) -> dict[str, dict[str, str]]:
    """Build general and provider-specialized prompts from one bounded brief."""
    brief = normalize_research_brief(research_brief, "general")
    query = (
        brief["query"]
        or brief["decision_question"]
        or (brief["entities"][0] if brief["entities"] else "")
    )
    if not query:
        raise ValueError("research brief must contain a query or entity")
    selected = tuple(source_ids) if source_ids is not None else tuple(_SOURCE_SUFFIXES)
    required = list(brief["required_dimensions"])
    extended = required + [
        dimension
        for dimension in brief["optional_dimensions"][:8]
        if dimension not in required
    ]
    general_core = _query_core(query, brief, required)
    specialized_core = _query_core(query, brief, extended)
    return {
        source_id: {
            "general": f"{general_core}\n{_GENERAL_SUFFIX}",
            "specialized": f"{specialized_core}\n{_SOURCE_SUFFIXES[source_id]}",
        }
        for source_id in selected
    }


def build_provider_queries(
    query: str,
    profile_name: str,
    research_brief: dict | None,
    source_ids: Iterable[str],
) -> dict[str, str]:
    supplied = dict(research_brief or {})
    supplied.setdefault("query", query.strip())
    brief = normalize_research_brief(supplied, profile_name)
    lanes = build_provider_query_lanes(brief, source_ids)
    return {
        source_id: provider_lanes["specialized"]
        for source_id, provider_lanes in lanes.items()
    }
