"""北京证券交易所（www.bse.cn）业务规则 adapter。"""

from __future__ import annotations

from chinalaw.adapters.securities_rules import (
    DEFAULT_TIMEOUT,
    SecuritiesRulesAdapter,
    SeedCandidate,
    SiteConfig,
)

CONFIG = SiteConfig(
    source="bse_cn",
    source_name="www.bse.cn",
    base_url="https://www.bse.cn",
    homepage_path="/node/latestRule.html",
    issuing_body="北京证券交易所",
    title_suffixes=(" - 北京证券交易所", "- 北京证券交易所"),
    content_markers=('class="in_main', 'id="fileDownload"', 'class="text_box"'),
    search_pages=(
        "/node/latestRule.html",
        "/business/fxrz_list.html",
        "/business/cxjg_list.html",
        "/business/jygl_list.html",
        "/business/scgl_list.html",
    ),
    search_api="bse",
    search_api_nodes=(
        "2885",  # 最新规则
        "1302",  # 股票 / 发行融资
        "1303",  # 股票 / 持续监管
        "1304",  # 股票 / 交易管理
        "1306",  # 市场管理
        "3130",  # 债券 / 发行融资
        "3131",  # 债券 / 持续监管
        "3132",  # 债券 / 交易管理
    ),
    seed_candidates=(
        SeedCandidate(
            title="北京证券交易所股票上市规则",
            detail_id="cxjg_list/200028220.html",
            released_at="2026-04-24",
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
