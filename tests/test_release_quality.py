from chinalaw import service
from chinalaw.aliases import common_law_aliases, preferred_short_title
from chinalaw.db import connect, migrate
from chinalaw.loader import load_law_from_dict
from tests.test_search_ranking import _law


def test_special_case_aliases_keep_ordinals():
    title = '最高人民法院关于审理劳动争议案件适用法律问题的解释（二）'
    assert preferred_short_title(title) == '劳动争议解释二'
    assert '劳动争议解释（二）' in common_law_aliases(title)
    assert '劳动争议解释' not in common_law_aliases(title)
    title = '最高人民法院关于审理发生在我国管辖海域相关案件若干问题的规定（一）'
    assert preferred_short_title(title).endswith('一')


def test_named_statute_clause_ranks_before_long_bundle(tmp_path):
    db = tmp_path / 'law.db'
    direct = _law('law', ['仲裁协议包括请求仲裁的意思表示。'], title='中华人民共和国仲裁法')
    bundle = _law('bundle', ['仲裁法 仲裁协议 请求仲裁的意思表示。' * 80], title='重新组建仲裁机构方案')
    bundle['articles'][0]['number'] = '正文'
    with connect(db) as conn:
        migrate(conn)
        load_law_from_dict(conn, direct)
        load_law_from_dict(conn, bundle)
    result = service.search(db, '仲裁法 仲裁协议 请求仲裁的意思表示', kind='article')
    assert result['article_hits'][0]['law_id'] == 'law'
    assert any(hit['law_id'] == 'bundle' for hit in result['article_hits'])


def test_same_source_hash_parser_correction_refreshes_body_and_index(tmp_path):
    from chinalaw.loader import refresh_law_metadata
    db = tmp_path / 'law.db'
    bad = _law('law', ['HYPERLINK 域代码残留'], source_hash='same-raw-source')
    fixed = _law('law', ['核验后的正确正文'], source_hash='same-raw-source')
    with connect(db) as conn:
        migrate(conn)
        load_law_from_dict(conn, bad)
        refresh_law_metadata(conn, fixed)
    assert service.get_article(db, 'law', '1')['article']['text'] == '核验后的正确正文'
    assert not service.search(db, 'HYPERLINK', kind='article')['article_hits']
    assert service.search(db, '正确正文', kind='article')['article_hits']


def test_same_date_revision_uses_current_verified_source(tmp_path):
    db = tmp_path / 'law.db'
    fixed = _law('law', ['核验正文'], source_hash='verified')
    bad = _law('law', ['错误旧副本'], source_hash='stale')
    with connect(db) as conn:
        migrate(conn)
        load_law_from_dict(conn, fixed)
        load_law_from_dict(conn, bad)
        load_law_from_dict(conn, fixed)
    result = service.get_article_as_of(db, 'law', '1', '2021-01-01')
    assert result['article']['text'] == '核验正文'
