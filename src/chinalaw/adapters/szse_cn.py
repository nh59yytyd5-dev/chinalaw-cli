"""深圳证券交易所（www.szse.cn）业务规则 adapter。"""

from __future__ import annotations

from chinalaw.adapters.securities_rules import (
    DEFAULT_TIMEOUT,
    SecuritiesRulesAdapter,
    SiteConfig,
)

CONFIG = SiteConfig(
    source="szse_cn",
    source_name="www.szse.cn",
    base_url="https://www.szse.cn",
    homepage_path="/lawrules/rule/new/index.html",
    issuing_body="深圳证券交易所",
    title_suffixes=("-深圳证券交易所", " | 深圳证券交易所"),
    content_markers=("W020", "附件：", "docpuburl"),
    search_pages=("/lawrules/rule/new/index.html",),
    search_api="szse",
    search_api_channel="szserulesAllRulesBuss",
)

default_adapter = SecuritiesRulesAdapter(CONFIG)


def probe(timeout: int = DEFAULT_TIMEOUT) -> dict:
    return SecuritiesRulesAdapter(CONFIG, timeout=timeout).probe()


def search_list(query: str | None = None, **kwargs) -> dict:
    return default_adapter.search_list(query, **kwargs)


def fetch_detail(detail_id: str) -> dict:
    return default_adapter.fetch_detail(detail_id)

