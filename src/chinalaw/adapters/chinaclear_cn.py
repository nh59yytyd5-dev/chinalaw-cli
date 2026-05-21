"""中国证券登记结算有限责任公司（chinaclear.cn）规则 adapter。"""

from __future__ import annotations

from chinalaw.adapters.securities_rules import (
    DEFAULT_TIMEOUT,
    SecuritiesRulesAdapter,
    SiteConfig,
)

CONFIG = SiteConfig(
    source="chinaclear_cn",
    source_name="www.chinaclear.cn",
    base_url="https://www.chinaclear.cn",
    homepage_path="/zdjs/flfg/law.shtml",
    issuing_body="中国证券登记结算有限责任公司",
    title_suffixes=("-中国证券登记结算有限责任公司",),
    content_markers=('class="newsdetail"', 'class="article"', 'class="content"'),
    search_pages=(
        "/zdjs/flfg/law.shtml",
        "/zdjs/fzhgl/law_flist.shtml",
        "/zdjs/fdjycg/law_flist.shtml",
        "/zdjs/fqsyjs/law_flist.shtml",
        "/zdjs/zqfb/law_flist.shtml",
        "/zdjs/zqyw/law_flist.shtml",
    ),
)

default_adapter = SecuritiesRulesAdapter(CONFIG)


def probe(timeout: int = DEFAULT_TIMEOUT) -> dict:
    return SecuritiesRulesAdapter(CONFIG, timeout=timeout).probe()


def search_list(query: str | None = None, **kwargs) -> dict:
    return default_adapter.search_list(query, **kwargs)


def fetch_detail(detail_id: str) -> dict:
    return default_adapter.fetch_detail(detail_id)

