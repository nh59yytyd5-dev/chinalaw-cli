"""Deleting one public-law row must clean every index and refuse dangling references."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from chinalaw import cli, loader, service
from chinalaw.db import connect, migrate

DUPLICATE = "court_main:zixun/xiangqing/310161"
KEPT = "spp_gov_cn:spp/zdgz/202106/t20210622_522041"


def _law(law_id: str, url: str, source_name: str) -> dict:
    return {
        "id": law_id,
        "title": "关于办理电信网络诈骗等刑事案件适用法律若干问题的意见（二）",
        "aliases": ["电诈意见（二）"],
        "level": "judicial_policy",
        "status": "unknown",
        "document_number": "法发〔2021〕22号",
        "source_url": url,
        "source_name": source_name,
        "categories": [{"id": "test:policy", "name": "司法政策"}],
        "category_ids": ["test:policy"],
        "articles": [
            {"number": "1", "text": "电信网络诈骗犯罪地包括用于犯罪活动的手机卡开立地。"},
            {"number": "2", "text": "为他人提供网络技术支持的，依法追究刑事责任。"},
        ],
    }


class LawDeleteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = Path(temporary.name) / "library.db"
        with connect(self.db) as conn:
            migrate(conn)
            loader.load_law_from_dict(conn, _law(
                DUPLICATE, "https://www.court.gov.cn/zixun/xiangqing/310161.html", "www.court.gov.cn",
            ))
            loader.load_law_from_dict(conn, _law(
                KEPT, "https://www.spp.gov.cn/spp/zdgz/202106/t20210622_522041.shtml", "www.spp.gov.cn",
            ))

    def _count(self, sql: str, *params) -> int:
        with connect(self.db) as conn:
            return int(conn.execute(sql, params).fetchone()[0])

    def _search_ids(self) -> set[str]:
        # Same-title copies fold by default; list every stored row.
        hits = service.search(
            self.db, "手机卡开立地", kind="article", versions="all"
        )["article_hits"]
        return {hit["law_id"] for hit in hits}

    def test_delete_removes_rows_and_indexes_but_keeps_the_other_copy(self):
        self.assertEqual(self._search_ids(), {DUPLICATE, KEPT})
        result = loader.delete_law(self.db, DUPLICATE)

        self.assertTrue(result["ok"])
        self.assertFalse(result["dry_run"])
        self.assertEqual(result["rows"]["articles"], 2)
        self.assertEqual(result["rows"]["revisions"], 1)
        self.assertEqual(result["rows"]["law_categories"], 1)
        self.assertEqual(result["rows"]["document_number_index"], 1)
        self.assertGreater(result["rows"]["law_alias_index"], 0)
        for table in ("laws", "articles", "revisions", "law_categories", "law_alias_index",
                      "document_number_index", "laws_fts_rows", "articles_fts_rows"):
            column = "id" if table == "laws" else "law_id"
            with self.subTest(table=table):
                self.assertEqual(
                    self._count(f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", DUPLICATE), 0
                )
        self.assertEqual(self._count("SELECT COUNT(*) FROM laws_fts WHERE law_id = ?", DUPLICATE), 0)
        self.assertEqual(self._search_ids(), {KEPT})
        laws = service.search(self.db, "电信网络诈骗", kind="law", versions="all")["law_hits"]
        self.assertEqual([hit["id"] for hit in laws], [KEPT])
        self.assertEqual(service.get_article(self.db, KEPT, "1")["law"]["id"], KEPT)

    def test_dry_run_reports_without_writing(self):
        result = loader.delete_law(self.db, DUPLICATE, dry_run=True)
        self.assertTrue(result["ok"] and result["dry_run"])
        self.assertEqual(result["rows"]["articles"], 2)
        self.assertEqual(self._count("SELECT COUNT(*) FROM laws WHERE id = ?", DUPLICATE), 1)
        self.assertEqual(self._search_ids(), {DUPLICATE, KEPT})

    def test_references_refuse_deletion(self):
        with connect(self.db) as conn:
            conn.execute(
                "INSERT INTO applicability_rules "
                "(id,topic,domain,primary_law_id,rule_text,source_name,source_checked_at) "
                "VALUES ('rule','测试主题','all',?,'测试规则','synthetic-test',"
                "'2020-01-01T00:00:00+00:00')",
                (DUPLICATE,),
            )
        result = loader.delete_law(self.db, DUPLICATE)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "law_referenced")
        self.assertEqual(
            result["references"],
            [{"table": "applicability_rules", "column": "primary_law_id", "count": 1}],
        )
        self.assertEqual(self._count("SELECT COUNT(*) FROM articles WHERE law_id = ?", DUPLICATE), 2)

    def test_only_exact_ids_are_accepted(self):
        self.assertIsNone(loader.delete_law(self.db, "关于办理电信网络诈骗等刑事案件适用法律若干问题的意见（二）"))
        self.assertIsNone(loader.delete_law(self.db, "court_main:zixun/xiangqing/999999"))
        self.assertEqual(self._count("SELECT COUNT(*) FROM laws"), 2)

    def test_cli_exit_codes_and_output(self):
        def run(*argv):
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli.app(["law", "delete", *argv, "--db", str(self.db)])
            return code, buffer.getvalue()

        code, out = run(DUPLICATE, "--dry-run")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["dry_run"])
        code, out = run(DUPLICATE)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["kind"], "law_delete")
        code, out = run(DUPLICATE)
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out)["found"])
        code, out = run(KEPT, "--format", "md")
        self.assertEqual(code, 0)
        self.assertIn("已删除公开法规", out)


if __name__ == "__main__":
    unittest.main()
