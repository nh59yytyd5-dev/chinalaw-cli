"""上海证券交易所（www.sse.com.cn）业务规则 adapter。"""

from __future__ import annotations

from chinalaw.adapters.securities_rules import (
    DEFAULT_TIMEOUT,
    SecuritiesRulesAdapter,
    SeedCandidate,
    SiteConfig,
)

CONFIG = SiteConfig(
    source="sse_com_cn",
    source_name="www.sse.com.cn",
    base_url="https://www.sse.com.cn",
    homepage_path="/lawandrules/index_app_new.shtml",
    issuing_body="上海证券交易所",
    title_suffixes=(" | 上海证券交易所", "| 上海证券交易所"),
    content_markers=('class="allZoom"', 'class="article-infor"'),
    search_pages=(
        "/lawandrules/index_app_new.shtml",
        "/services/listingwithsse/home/policy/supervise/",
    ),
    # 上交所部分高频规则不在公开栏目页首屏列表内，但详情页稳定可直取。
    seed_candidates=(
        SeedCandidate(
            title="上海证券交易所股票上市规则（2025年4月修订）",
            detail_id="services/listingwithsse/home/policy/supervise/c/c_20250425_10777756.shtml",
            released_at="2025-04-25",
        ),
    ),
)

default_adapter = SecuritiesRulesAdapter(CONFIG)


def probe(timeout: int = DEFAULT_TIMEOUT) -> dict:
    return SecuritiesRulesAdapter(CONFIG, timeout=timeout).probe()


def search_list(query: str | None = None, **kwargs) -> dict:
    return default_adapter.search_list(query, **kwargs)


def fetch_detail(detail_id: str) -> dict:
    return default_adapter.fetch_detail(detail_id)

