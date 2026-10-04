"""Literal fallback must remain bounded, explicit, and scoped."""
from unittest import mock

from chinalaw import formatters, service
from chinalaw.fuzzy import fragments, matching_fragments
from tests.test_search_ranking import SearchTests, _law


class FuzzyTests(SearchTests):
    def test_duplicate_candidates_share_work_but_not_mutable_results(self):
        matched = '公司作出决议并提供担保'
        missed = '公司另行规定'
        self.load(_law('dup', [matched, matched, missed, missed]))
        with mock.patch.object(service, 'matching_fragments', wraps=matching_fragments) as check:
            result = service.search(self.db, '公司担保决议', kind='article', versions='all')
        self.assertEqual(check.call_count, 2)
        first, second = result['article_hits']
        self.assertEqual(first['fuzzy']['matched'], second['fuzzy']['matched'])
        first['fuzzy']['matched'].append('仅修改第一条')
        self.assertNotIn('仅修改第一条', second['fuzzy']['matched'])
        self.assertEqual(service.search(self.db, '公司返还投资')['counts']['total'], 0)
        self.load(_law('dup', [missed, missed]))
        self.assertEqual(service.search(self.db, '公司担保决议')['counts']['total'], 0)

    def test_complete_fragments_in_one_article(self):
        self.load(_law('a', ['公司担保决议', '公司作出决议并提供担保', '公司', '担保决议']))
        result = service.search(self.db, '公司担保决议', kind='article')
        hits = result['article_hits']
        self.assertEqual([(h['number'], h['match_mode']) for h in hits],
                         [('1', 'exact'), ('2', 'fuzzy')])
        self.assertEqual(''.join(hits[1]['fuzzy']['matched']), '公司担保决议')
        self.assertIn('近似匹配', formatters.search_to_markdown(result))
        self.assertEqual(result['fuzzy']['count'], 1)
        limited = service.search(self.db, '公司担保决议', kind='article', limit=1)
        self.assertFalse(limited['fuzzy']['applied'])

    def test_threshold_and_scope(self):
        self.load(_law('a', ['公司担保决议'] * 5 + ['公司作出决议并提供担保']),
                  _law('b', ['公司作出决议并提供担保'], status='repealed'))
        result = service.search(self.db, '公司担保决议', kind='article')
        self.assertFalse(result['fuzzy']['applied'])
        result = service.search(self.db, '公司，担保，决议', kind='article', in_laws='b')
        self.assertEqual({h['law_id'] for h in result['article_hits']}, {'b'})
        result = service.search(self.db, '公司，担保，决议', kind='article',
                                in_laws='b', status='current')
        self.assertEqual(result['counts']['total'], 0)
        result = service.search(self.db, '公司，担保，决议', kind='article', in_part='不存在')
        self.assertEqual(result['counts']['total'], 0)

    def test_no_spelling_guess_or_prefix_truncation(self):
        self.load(_law('a', ['定金应当返还；超越权限订立合同。']))
        result = service.search(self.db, '订金', kind='article')
        self.assertEqual(result['counts']['total'], 0)
        self.assertIn('法条原文', result['hint'])
        self.assertIn('法条原文', formatters.search_to_markdown(result))
        self.assertEqual(fragments('公司' * 41), [])
        self.assertEqual(fragments('5%，违约金'), ['5%', '违约金'])
        self.assertIsNone(matching_fragments(fragments('5%，违约金'), '5元违约金'))
        self.assertIsNone(matching_fragments(['超越权限错误'], '超越，权限'))
        self.assertIsNone(matching_fragments(['表现代理'], '表见代理'))
        self.assertIsNone(matching_fragments(['表现代理'], '考察表现，听取诉讼代理人的意见'))
        self.assertEqual(matching_fragments(['二倍工资'], '支付二倍的工资'), ['二倍', '工资'])

    def test_candidates_do_not_resolve_or_take_articles(self):
        self.load(_law('privacy', ['个人信息受到保护。'], title='中华人民共和国个人信息保护法'))
        result = service.resolve(self.db, '个保法')
        self.assertFalse(result['matched'])
        self.assertEqual(result['candidates'][0]['id'], 'privacy')
        self.assertIn('未自动采用', formatters.resolve_to_markdown(result))
        self.assertIsNone(service.get_article(self.db, '个保法', '1'))
        miss = service.diagnose_article_miss(self.db, '个保法', '1')
        self.assertEqual(miss['candidate_laws'][0]['id'], 'privacy')
        miss = service.get_articles(self.db, '个保法', '1')
        self.assertEqual(miss['candidate_laws'][0]['id'], 'privacy')
        result = service.search(self.db, '个人信息', in_laws='个保法')
        self.assertEqual(result['counts']['total'], 0)
        self.assertEqual(result['law_filter']['unresolved_candidates']['个保法'][0]['id'], 'privacy')

    def test_fuzzy_keeps_exact_work_version(self):
        self.load(
            _law('old', ['公司担保决议'], title='同名测试法', effective_at='2020-01-01'),
            _law('new', ['公司作出决议并提供担保'], title='同名测试法', effective_at='2025-01-01'),
        )
        result = service.search(self.db, '公司担保决议', kind='article')
        self.assertEqual([(h['law_id'], h['match_mode']) for h in result['article_hits']],
                         [('old', 'exact')])
        result = service.search(self.db, '公司担保决议', kind='article', versions='all')
        self.assertEqual([(h['law_id'], h['match_mode']) for h in result['article_hits']],
                         [('old', 'exact'), ('new', 'fuzzy')])

    def test_name_candidate_prefers_contiguous_match(self):
        self.load(_law('civil', ['测试'], title='中华人民共和国民法典'),
                  _law('procedure', ['测试'], title='中华人民共和国民事诉讼法',
                       aliases=['民诉法']))
        result = service.resolve(self.db, '民法点')
        self.assertFalse(result['matched'])
        self.assertEqual(result['candidates'][0]['id'], 'civil')
