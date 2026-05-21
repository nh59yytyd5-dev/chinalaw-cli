"""中国证券业协会（sac.net.cn）自律规则 adapter。"""

from __future__ import annotations

from chinalaw.adapters.securities_rules import (
    DEFAULT_TIMEOUT,
    SecuritiesRulesAdapter,
    SiteConfig,
)

CONFIG = SiteConfig(
    source="sac_net_cn",
    source_name="www.sac.net.cn",
    base_url="https://www.sac.net.cn",
    homepage_path="/sjb/flfg_949/zlgz/index.html",
    issuing_body="中国证券业协会",
    title_suffixes=("-中国证券协会", "-中国证券业协会"),
    content_markers=('class="TRS_Editor"', 'class="content"', 'class="notice_con"'),
    search_pages=(
        "/flfg/zlgz/index.html",
        "/flfg/zlgz/index_1.html",
        "/flfg/zlgz/index_2.html",
        "/sjb/flfg_949/zlgz/index.html",
        "/sjb/flfg_949/zlgz/index_1.html",
        "/sjb/flfg_949/zlgz/index_2.html",
    ),
    paginated_search_roots=(
        "/flfg/zlgz",
    ),
    paginated_search_max_pages=14,
    search_deadline_seconds=25,
)

default_adapter = SecuritiesRulesAdapter(CONFIG)


def probe(timeout: int = DEFAULT_TIMEOUT) -> dict:
    return SecuritiesRulesAdapter(CONFIG, timeout=timeout).probe()


def search_list(query: str | None = None, **kwargs) -> dict:
    return default_adapter.search_list(query, **kwargs)


def fetch_detail(detail_id: str) -> dict:
    return default_adapter.fetch_detail(detail_id)
