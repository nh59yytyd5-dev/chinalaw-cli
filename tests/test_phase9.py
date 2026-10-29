from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chinalaw import PROJECT_URL, USER_AGENT_TOKEN, cli, formatters, loader, service, sources
from chinalaw.db import connect, migrate, open_connection
from chinalaw.normsources import import_source_from_dict
from chinalaw.schema import SCHEMA_V10_SQL, SCHEMA_VERSION


def _law_payload() -> dict:
    return {
        "id": "company-law-index-test",
        "title": "中华人民共和国公司法",
        "short_title": None,
        "aliases": [],
        "level": "law",
        "status": "current",
        "released_at": "2023-12-29",
        "effective_at": "2024-07-01",
        "source_url": "https://example.test/company-law",
        "source_name": "example.test",
        "source_checked_at": "2026-08-06T00:00:00+08:00",
        "articles": [
            {
                "number": "1",
                "number_display": "第一条",
                "part": "第一章 总则",
                "text": "为了规范公司的组织和行为，制定本法。",
            }
        ],
    }


class FormatterConsistencyTests(unittest.TestCase):
    def _law(self) -> dict:
        current = {"version_label": "2024-07-01 施行版"}
        return {
            "title": "示例法",
            "short_title": "示例法",
            "status": "repealed",
            "effective_at": "2020-01-01",
            "repealed_at": "2025-01-01",
            "source_url": "https://example.test/law",
            "source_checked_at": "2026-08-01T00:00:00+08:00",
            "freshness_days": 5,
            "current_revision": current,
            "selected_revision": current,
        }

    def test_law_markdown_emits_each_consecutive_part_once(self) -> None:
        law = {
            **self._law(),
            "level": "law",
            "articles": [
                {
                    "number_display": "第一条",
                    "part": "第一章 总则",
                    "text": "第一条正文。",
                },
                {
                    "number_display": "第二条",
                    "part": "第一章 总则",
                    "text": "第二条正文。",
                },
                {
                    "number_display": "第三条",
                    "part": "第二章 规则",
                    "text": "第三条正文。",
                },
            ],
        }
        rendered = formatters.law_to_markdown(law)
        self.assertEqual(rendered.count("### 第一章 总则"), 1)
        self.assertEqual(rendered.count("### 第二章 规则"), 1)
        self.assertIn("- 废止日期：2025-01-01", rendered)

    def test_full_footer_is_shared_by_article_batch_and_outline(self) -> None:
        law = self._law()
        article = {
            "number": "1",
            "number_display": "第一条",
            "text": "正文。",
        }
        outputs = (
            formatters.article_to_markdown({"law": law, "article": article}),
            formatters.articles_to_markdown(
                {
                    "law": law,
                    "item_count": 1,
                    "found_count": 1,
                    "missing_count": 0,
                    "items": [{"article": article}],
                }
            ),
            formatters.outline_to_markdown_with_text(
                {
                    "law": law,
                    "article_count": 1,
                    "item_count": 1,
                    "items": [{"article": article}],
                }
            ),
        )
        for rendered in outputs:
            with self.subTest(rendered=rendered[:30]):
                self.assertIn("- 文本状态：repealed", rendered)
                self.assertIn("- 施行日期：2020-01-01", rendered)
                self.assertIn("- 废止日期：2025-01-01", rendered)
                self.assertIn("- 当前版本：2024-07-01 施行版", rendered)
                self.assertIn("- 来源：https://example.test/law", rendered)
                self.assertIn("- 最后核查：核查 5 天前", rendered)


class SearchIndexMigrationTests(unittest.TestCase):
    def test_v10_migration_backfills_aliases_and_deduplicates_fts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"
            conn = open_connection(db_path)
            try:
                conn.executescript(SCHEMA_V10_SQL)
                conn.execute(
                    """
                    INSERT INTO laws (
                        id, title, short_title, aliases, level, status,
                        source_url, source_name, source_checked_at, source_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "legacy-company-law",
                        "中华人民共和国公司法",
                        None,
                        "[]",
                        "law",
                        "current",
                        "https://example.test/company-law",
                        "example.test",
                        "2026-08-06T00:00:00+08:00",
                        "legacy-hash",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO articles (
                        id, law_id, number, number_display, text, position
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "legacy-company-law#1",
                        "legacy-company-law",
                        "1",
                        "第一条",
                        "公司法正文。",
                        1,
                    ),
                )
                for _ in range(2):
                    conn.execute(
                        "INSERT INTO laws_fts(law_id, title, short_title, aliases) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            "legacy-company-law",
                            "中华人民共和国公司法",
                            "",
                            "",
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO articles_fts(
                            article_id, law_id, law_title, number_display, text
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            "legacy-company-law#1",
                            "legacy-company-law",
                            "中华人民共和国公司法",
                            "第一条",
                            "公司法正文。",
                        ),
                    )
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES('schema_version', '10')"
                )
                conn.commit()

                self.assertEqual(migrate(conn), SCHEMA_VERSION)
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM laws_fts").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM articles_fts").fetchone()[0],
                    1,
                )
                alias = conn.execute(
                    "SELECT kind FROM law_alias_index WHERE alias = ?",
                    ("公司法",),
                ).fetchone()
                self.assertEqual(alias["kind"], "derived")
            finally:
                conn.close()

            resolved = service.resolve(db_path, "公司法")
            self.assertTrue(resolved["matched"])
            self.assertEqual(resolved["via"], "alias_derived")

    def test_upserts_keep_one_fts_row_and_use_alias_lookup_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "indexes.db"
            with connect(db_path) as conn:
                migrate(conn)
                payload = _law_payload()
                loader.load_law_from_dict(conn, payload)
                loader.load_law_from_dict(conn, payload)

                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM laws_fts").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM articles_fts").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM laws_fts_rows").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM articles_fts_rows").fetchone()[0],
                    1,
                )
                plan = " ".join(
                    row[3]
                    for row in conn.execute(
                        "EXPLAIN QUERY PLAN SELECT law_id FROM law_alias_index "
                        "WHERE alias = ? AND kind = ?",
                        ("公司法", "derived"),
                    )
                )
                self.assertIn("idx_law_alias_index_lookup", plan)

    def test_norm_source_upsert_keeps_one_fts_row_per_record(self) -> None:
        payload = {
            "id": "credit-policy",
            "name": "授信政策",
            "aliases": ["放款政策"],
            "clauses": [
                {"number": "1", "number_display": "第一条", "text": "审查放款条件。"}
            ],
        }
        with (
            tempfile.TemporaryDirectory() as tmp,
            connect(Path(tmp) / "norm.db") as conn,
        ):
            import_source_from_dict(conn, payload)
            import_source_from_dict(conn, payload)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM norm_sources_fts").fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM norm_clauses_fts").fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM norm_sources_fts_rows").fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM norm_clauses_fts_rows").fetchone()[0],
                1,
            )


class FetchThrottleTests(unittest.TestCase):
    def tearDown(self) -> None:
        for name, interval in sources._DEFAULT_REQUEST_INTERVALS.items():
            sources.ADAPTER_REGISTRY[name].request_interval = interval

    def test_environment_applies_at_least_two_seconds_to_every_adapter(self) -> None:
        with patch.dict(os.environ, {"CHINALAW_FETCH_THROTTLE_MS": "2000"}):
            for name in sources.ADAPTER_REGISTRY:
                with self.subTest(source=name):
                    self.assertGreaterEqual(
                        sources.get_source_adapter(name).request_interval,
                        2.0,
                    )

    def test_lookup_resets_to_default_and_rejects_invalid_values(self) -> None:
        name = "flk_npc"
        with patch.dict(os.environ, {"CHINALAW_FETCH_THROTTLE_MS": "2000"}):
            self.assertEqual(sources.get_source_adapter(name).request_interval, 2.0)

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CHINALAW_FETCH_THROTTLE_MS", None)
            self.assertEqual(
                sources.get_source_adapter(name).request_interval,
                sources._DEFAULT_REQUEST_INTERVALS[name],
            )

        for value in ("slow", "nan", "inf", "-1"):
            with self.subTest(value=value), patch.dict(
                os.environ,
                {"CHINALAW_FETCH_THROTTLE_MS": value},
            ), self.assertRaises(ValueError):
                    sources.get_source_adapter(name)


class DocumentationContractTests(unittest.TestCase):
    def test_docs_state_current_schema_version_and_baseline_counts(self) -> None:
        """文档写死的 schema 版本与随包基线规模要跟着实现走（#49）。"""
        repo_root = Path(__file__).resolve().parents[1]
        architecture = (repo_root / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
        guide = (repo_root / "docs" / "DEVELOPMENT_GUIDE.md").read_text(encoding="utf-8")
        self.assertIn(f"当前 schema 版本是 v{SCHEMA_VERSION}", architecture)
        self.assertIn(f"当前 schema v{SCHEMA_VERSION} DDL", architecture)
        self.assertIn(f"当前为 v{SCHEMA_VERSION}", guide)

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            result = loader.load_fixtures(db_path, loader.FIXTURES_DIR)
            status = service.status(db_path)
        texts = result["laws_loaded"]
        counts = f"{status['laws']} 部法规、{status['articles']} 条条文、{status['revisions']} 份版本"
        readme = (repo_root / "README.md").read_text(encoding="utf-8")
        readme_en = (repo_root / "README.en.md").read_text(encoding="utf-8")
        data_index = (repo_root / "docs" / "DATA_INDEX.md").read_text(encoding="utf-8")
        self.assertIn(f"内置 {texts} 份", readme)
        self.assertIn(f"入库为 {status['laws']} 部法规", readme)
        self.assertIn(f"入库后为 {counts}", readme)
        self.assertIn(f"the {texts} bundled public texts ({status['laws']} laws once loaded)", readme_en)
        self.assertIn(
            f"{status['laws']} laws, {status['articles']:,} articles and {status['revisions']} versions",
            readme_en,
        )
        self.assertIn(f"包含 {texts} 个完整 fixture", data_index)
        self.assertIn(counts, data_index.replace("\n", ""))

    def test_readmes_state_public_snapshot_counts(self) -> None:
        # 快照规模以随发布包维护的质量记录为准，两种语言的 README 都跟着它走。
        repo_root = Path(__file__).resolve().parents[1]
        quality = (repo_root / "data" / "public-release" / "QUALITY.md").read_text(encoding="utf-8")
        laws, articles, revisions = (
            int(n) for n in re.search(r"公开库(\d+)条法规记录、(\d+)条条文、(\d+)份修订", quality).groups()
        )
        readme = (repo_root / "README.md").read_text(encoding="utf-8")
        readme_en = (repo_root / "README.en.md").read_text(encoding="utf-8")
        self.assertIn(f"{laws} 条法规记录、{articles} 条条文记录、{revisions} 份版本", readme)
        self.assertIn(
            f"{laws:,} law records, {articles:,} article records and {revisions:,} versions", readme_en
        )

    def test_english_readme_mirrors_chinese_readme(self) -> None:
        # 英文版是中文 README 的译本：互相链接，章节层次一致，示例命令逐行相同（注释除外）。
        repo_root = Path(__file__).resolve().parents[1]
        readme = (repo_root / "README.md").read_text(encoding="utf-8")
        readme_en = (repo_root / "README.en.md").read_text(encoding="utf-8")
        self.assertIn('href="README.en.md"', readme)
        self.assertIn('href="README.md"', readme_en)

        def outline(text: str) -> list[str]:
            prose = re.sub(r"^```.*?^```", "", text, flags=re.M | re.S)
            return [line.split(" ", 1)[0] for line in prose.splitlines() if re.match(r"#{2,3} ", line)]

        def code_blocks(text: str) -> list[tuple[str, list[str]]]:
            blocks = []
            for language, body in re.findall(r"^```(\w*)\n(.*?)^```", text, flags=re.M | re.S):
                if language == "text":  # 给 agent 的规则随语言翻译
                    blocks.append((language, []))
                    continue
                lines = [line.split(" #", 1)[0].rstrip() for line in body.splitlines()]
                blocks.append((language, [line for line in lines if line and not line.startswith("#")]))
            return blocks

        self.assertEqual(outline(readme), outline(readme_en))
        self.assertEqual(code_blocks(readme), code_blocks(readme_en))

    def test_active_markdown_links_resolve(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        documents = [repo_root / "README.md", repo_root / "README.en.md", repo_root / "NOTICES.md"]
        documents.extend(sorted((repo_root / "docs").glob("*.md")))
        missing: list[str] = []
        for document in documents:
            text = document.read_text(encoding="utf-8")
            for raw_target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
                target = raw_target.strip().split("#", 1)[0]
                if not target or "://" in target or target.startswith(("#", "mailto:")):
                    continue
                if not (document.parent / target).resolve().exists():
                    missing.append(f"{document.relative_to(repo_root)} -> {target}")
        self.assertEqual(missing, [])

    def test_user_agent_and_compliance_use_real_project_and_source_catalog(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        compliance = (repo_root / "docs" / "COMPLIANCE.md").read_text(encoding="utf-8")
        notices = (repo_root / "NOTICES.md").read_text(encoding="utf-8")
        source_catalog = json.loads(
            (repo_root / "data" / "source_coverage.json").read_text(encoding="utf-8")
        )

        self.assertEqual(USER_AGENT_TOKEN, f"chinalaw-cli/0.7.6 (+{PROJECT_URL})")
        self.assertIn(PROJECT_URL, compliance)
        self.assertNotIn("github.com/chinalaw-cli/chinalaw-cli", compliance)
        self.assertNotIn("README 中维护者邮箱", compliance)
        self.assertNotIn("http://gongbao.court.gov.cn", notices)
        for source in source_catalog["sources"]:
            if source.get("adapter_status") == "implemented":
                self.assertIn(source["id"], compliance)

    def test_skill_copies_match_current_cli_contract(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        skill_relatives = (
            "chinalaw-using/SKILL.md",
            "chinalaw-fetching/SKILL.md",
            "chinalaw-maintaining/SKILL.md",
            "chinalaw-maintaining/scripts/doctor.sh",
            "chinalaw-searching/SKILL.md",
            "chinalaw-checking/SKILL.md",
            "contract-review/SKILL.md",
            "legal-research/SKILL.md",
        )
        for relative in skill_relatives:
            claude = (repo_root / ".claude" / "skills" / relative).read_text(
                encoding="utf-8"
            )
            agents = (repo_root / ".agents" / "skills" / relative).read_text(
                encoding="utf-8"
            )
            self.assertEqual(claude, agents, relative)

        combined = "\n".join(
            (repo_root / ".claude" / "skills" / relative).read_text(
                encoding="utf-8"
            )
            for relative in skill_relatives
        )
        for forbidden in (
            "--in-laws",
            "rebuild-clean --force",
            "stub_only",
            "fts_status",
            "source_freshness",
        ):
            self.assertNotIn(forbidden, combined)

        skill_root = repo_root / ".claude" / "skills"
        searching = (skill_root / "chinalaw-searching" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        checking = (skill_root / "chinalaw-checking" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        contract = (skill_root / "contract-review" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        research = (skill_root / "legal-research" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn('--in-part "第一编 总则"', searching)
        self.assertIn('--part "第三编 合同 第一分编 通则 第六章 合同的变更和转让"', searching)
        self.assertNotIn("公司法（2023 修订） 第32条", searching)
        self.assertIn("当前契约没有 `revision_id` 字段", checking)
        self.assertNotIn("PYTHONPATH=src", contract)
        self.assertNotIn("--prefer-bbbs", contract + research)
        self.assertNotIn("CHINALAW_HEADLESS", research)
        self.assertNotIn("Headless 模式", research)

        parser = cli.build_parser()
        parser.parse_args(["search", "合同", "--in", "民法典", "--kind", "article"])
        parser.parse_args(["rebuild-clean", "--law", "民法典"])
        parser.parse_args(["article", "flk-company-law-2024", "32"])
        parser.parse_args(
            [
                "outline",
                "民法典",
                "--part",
                "第三编 合同 第一分编 通则 第六章 合同的变更和转让",
                "--full-text",
            ]
        )


if __name__ == "__main__":
    unittest.main()
