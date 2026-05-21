"""Regression tests for the DeepSeek harness stream analyzer."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent
ANALYZE_PATH = REPO_ROOT / "scripts" / "eval" / "analyze.py"


def _load_analyze_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("eval_analyze", ANALYZE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(ANALYZE_PATH.exists(), "optional eval analyzer script is not included")
class EvalAnalyzeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.analyze_mod = _load_analyze_module()

    def test_parse_chinalaw_subcmd_skips_option_values(self):
        parse = self.analyze_mod._parse_chinalaw_subcmd

        self.assertEqual(
            parse("PYTHONPATH=src chinalaw --db /tmp/chinalaw.db search 工作时间"),
            "search",
        )
        self.assertEqual(
            parse("python3 -m chinalaw --db /tmp/chinalaw.db article 民法典 143"),
            "article",
        )
        self.assertEqual(
            parse(
                "bash -lc 'chinalaw --db /tmp/db applicable "
                "--date 2019-01-01 --topic 合同'"
            ),
            "applicable",
        )

    def test_timestamp_seconds_accepts_iso_stream_timestamps(self):
        parse_ts = self.analyze_mod._timestamp_seconds

        start = parse_ts("2026-05-03T09:34:21.437Z")
        end = parse_ts("2026-05-03T09:34:26.937Z")

        self.assertIsNotNone(start)
        self.assertIsNotNone(end)
        self.assertAlmostEqual(end - start, 5.5)

    def test_analyze_scans_result_text_not_user_prompt_or_tool_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            qdir = Path(tmp)
            (qdir / "question.txt").write_text(
                "民事\t用户题目含《民法典》第一条 needs_fetch\n",
                encoding="utf-8",
            )
            (qdir / "stderr.log").write_text("exit=0 status=ok\n", encoding="utf-8")
            events = [
                {
                    "type": "user",
                    "timestamp": "2026-05-03T09:34:21.437Z",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "提示词里的《民法典》第一条 needs_fetch 不应统计",
                            }
                        ],
                    },
                },
                {
                    "type": "assistant",
                    "timestamp": "2026-05-03T09:34:24.437Z",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "中间话术《刑法》第三条"}
                        ],
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "content": "工具输出《公司法》第四条 law_missing",
                            }
                        ],
                    },
                },
                {
                    "type": "result",
                    "timestamp": "2026-05-03T09:34:26.937Z",
                    "result": "最终答案引用《劳动法》第三十六条 not_legal_conclusion",
                },
            ]
            (qdir / "stream.jsonl").write_text(
                "\n".join(json.dumps(ev, ensure_ascii=False) for ev in events) + "\n",
                encoding="utf-8",
            )

            original_verify = self.analyze_mod._verify_citations
            self.analyze_mod._verify_citations = lambda citations: [
                {"law": law, "number": num, "verified": True, "reason": ""}
                for law, num in citations
            ]
            try:
                result = self.analyze_mod.analyze(qdir)
            finally:
                self.analyze_mod._verify_citations = original_verify

            self.assertEqual(result["final_text_chars"], len(events[-1]["result"]))
            self.assertAlmostEqual(result["wall_seconds"], 5.5)
            self.assertEqual(
                result["cited_articles"],
                [{"law": "劳动法", "number": "三十六", "verified": True, "reason": ""}],
            )
            self.assertEqual(result["degradation_used"], ["not_legal_conclusion"])

    def test_extract_citations_keeps_common_bare_citations(self):
        extract = self.analyze_mod._extract_citations

        self.assertIn(("公司法", "88"), extract("公司法第88条规定了相关规则。"))
        self.assertIn(("公司法", "88"), extract("2018 年公司法第88条仍需核对。"))
        self.assertIn(("民法典", "152"), extract("**民法典第152条** 是除斥期间规则。"))

    def test_extract_citations_does_not_overcapture_bare_context(self):
        extract = self.analyze_mod._extract_citations

        citations = extract("公司可依据公司法第八十八条主张责任。")
        self.assertIn(("公司法", "八十八"), citations)
        self.assertNotIn(("公司可依据公司法", "八十八"), citations)

    def test_extract_citations_does_not_treat_transition_verbs_as_law_names(self):
        extract = self.analyze_mod._extract_citations

        text = (
            "已获取核心条文。现在尝试获取刑法第三十条立法解释的正文，"
            "同时查贪污贿赂司法解释第十一条。"
        )
        citations = extract(text)
        self.assertNotIn(("现在尝试获取刑法", "三十"), citations)
        self.assertNotIn(("同时查贪污贿赂司法解释", "十一"), citations)

    def test_extract_citations_does_not_capture_anaphora_as_law_name(self):
        extract = self.analyze_mod._extract_citations

        citations = extract("但该解释第34条体现的规则仍需核对。")
        self.assertNotIn(("但该解释", "34"), citations)

    def test_extract_citations_strips_inline_legal_leaders(self):
        extract = self.analyze_mod._extract_citations

        citations = extract("债务加入可按担保制度解释第36条认定。")
        self.assertIn(("担保制度解释", "36"), citations)
        self.assertNotIn(("按担保制度解释", "36"), citations)

    def test_extract_skill_refs_uses_repo_skill_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            skills_dir = Path(tmp) / "skills"
            (skills_dir / "new-agent-skill").mkdir(parents=True)
            (skills_dir / "new-agent-skill" / "SKILL.md").write_text(
                "# skill\n",
                encoding="utf-8",
            )

            original_dir = self.analyze_mod._SKILLS_DIR
            self.analyze_mod._SKILLS_DIR = skills_dir
            self.analyze_mod._known_skill_names.cache_clear()
            try:
                refs = self.analyze_mod._extract_skill_refs(
                    'tool call: skill: new-agent-skill; skill: deleted-skill'
                )
            finally:
                self.analyze_mod._SKILLS_DIR = original_dir
                self.analyze_mod._known_skill_names.cache_clear()

        self.assertEqual(["new-agent-skill"], refs)

    def test_analyze_classifies_error_max_turns_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            qdir = Path(tmp)
            (qdir / "question.txt").write_text("劳动\t二倍工资\n", encoding="utf-8")
            (qdir / "stderr.log").write_text(
                "exit=1 status=error_rc_1\n", encoding="utf-8"
            )
            events = [
                {
                    "type": "result",
                    "subtype": "error_max_turns",
                    "is_error": True,
                    "num_turns": 26,
                    "stop_reason": "tool_use",
                    "terminal_reason": "max_turns",
                    "errors": ["Reached maximum number of turns (25)"],
                }
            ]
            (qdir / "stream.jsonl").write_text(
                "\n".join(json.dumps(ev, ensure_ascii=False) for ev in events) + "\n",
                encoding="utf-8",
            )

            original_verify = self.analyze_mod._verify_citations
            self.analyze_mod._verify_citations = lambda citations: []
            try:
                result = self.analyze_mod.analyze(qdir)
            finally:
                self.analyze_mod._verify_citations = original_verify

            self.assertEqual(result["error_type"], "max_turns")
            self.assertEqual(result["result_subtype"], "error_max_turns")
            self.assertEqual(result["terminal_reason"], "max_turns")

    def test_analyze_understands_opencode_json_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            qdir = Path(tmp)
            (qdir / "question.txt").write_text("民事\t核对民法典143条\n", encoding="utf-8")
            (qdir / "stderr.log").write_text(
                "exit=0 status=ok\nwall_seconds=4\n",
                encoding="utf-8",
            )
            events = [
                {
                    "type": "step_start",
                    "timestamp": 1778923030000,
                    "part": {"type": "step-start"},
                },
                {
                    "type": "tool_use",
                    "timestamp": 1778923030100,
                    "part": {
                        "type": "tool",
                        "tool": "bash",
                        "callID": "call_1",
                        "state": {
                            "status": "completed",
                            "input": {
                                "command": "chinalaw article 民法典 143 --format json",
                                "description": "Check article",
                            },
                            "output": json.dumps(
                                {
                                    "law": {"id": "flk-civil-code-2020"},
                                    "article": {"number": "143"},
                                }
                            ),
                        },
                    },
                },
                {
                    "type": "step_finish",
                    "timestamp": 1778923030200,
                    "part": {
                        "type": "step-finish",
                        "reason": "tool-calls",
                        "tokens": {
                            "input": 100,
                            "output": 10,
                            "reasoning": 3,
                            "cache": {"read": 5, "write": 7},
                        },
                    },
                },
                {
                    "type": "step_start",
                    "timestamp": 1778923030300,
                    "part": {"type": "step-start"},
                },
                {
                    "type": "text",
                    "timestamp": 1778923030400,
                    "part": {
                        "type": "text",
                        "text": "最终引用《民法典》第一百四十三条。",
                    },
                },
                {
                    "type": "step_finish",
                    "timestamp": 1778923030500,
                    "part": {
                        "type": "step-finish",
                        "reason": "stop",
                        "tokens": {
                            "input": 120,
                            "output": 8,
                            "reasoning": 2,
                            "cache": {"read": 0, "write": 0},
                        },
                    },
                },
            ]
            (qdir / "stream.jsonl").write_text(
                "\n".join(json.dumps(ev, ensure_ascii=False) for ev in events) + "\n",
                encoding="utf-8",
            )

            original_verify = self.analyze_mod._verify_citations
            self.analyze_mod._verify_citations = lambda citations: [
                {"law": law, "number": num, "verified": True, "reason": "db_match"}
                for law, num in citations
            ]
            try:
                result = self.analyze_mod.analyze(qdir)
            finally:
                self.analyze_mod._verify_citations = original_verify

            self.assertEqual(result["tool_calls"], {"bash": 1})
            self.assertEqual(result["chinalaw_calls"], {"article": 1})
            self.assertEqual(result["chinalaw_call_chain"][0]["result_summary"], "ok")
            self.assertEqual(result["wall_seconds"], 4.0)
            self.assertEqual(result["usage"]["turns"], 2)
            self.assertEqual(result["usage"]["fresh_in"], 220)
            self.assertEqual(result["usage"]["cache_read"], 5)
            self.assertEqual(result["usage"]["cache_create"], 7)
            self.assertEqual(result["usage"]["output"], 18)
            self.assertEqual(result["stop_reason"], "stop")


@unittest.skipUnless(ANALYZE_PATH.exists(), "optional eval analyzer script is not included")
class CallChainExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_analyze_module()

    def test_parse_invocation_extracts_subcmd_args_flags(self):
        parse = self.mod._parse_chinalaw_invocation
        inv = parse("chinalaw fetch '中华人民共和国担保法' --list-matches --format md")
        assert inv is not None
        self.assertEqual(inv["subcmd"], "fetch")
        self.assertEqual(inv["args"], ["中华人民共和国担保法"])
        self.assertEqual(inv["flags"].get("--format"), "md")
        self.assertEqual(inv["flags"].get("--list-matches"), "true")

    def test_parse_invocation_handles_equals_form(self):
        parse = self.mod._parse_chinalaw_invocation
        inv = parse("chinalaw search 公序良俗 --kind=article --format=json")
        assert inv is not None
        self.assertEqual(inv["subcmd"], "search")
        self.assertEqual(inv["args"], ["公序良俗"])
        self.assertEqual(inv["flags"]["--kind"], "article")
        self.assertEqual(inv["flags"]["--format"], "json")

    def test_parse_invocation_ignores_shell_redirection(self):
        parse = self.mod._parse_chinalaw_invocation
        inv = parse(
            "chinalaw search '保证期间届满 签字' 2>/dev/null --kind all --format json"
        )
        assert inv is not None
        self.assertEqual(inv["subcmd"], "search")
        self.assertEqual(inv["args"], ["保证期间届满 签字"])
        self.assertEqual(inv["flags"]["--kind"], "all")
        self.assertEqual(inv["flags"]["--format"], "json")

    def test_parse_invocation_skips_global_options_before_subcmd(self):
        parse = self.mod._parse_chinalaw_invocation
        inv = parse("chinalaw --db /tmp/chinalaw.db article 民法典 143 --format json")
        assert inv is not None
        self.assertEqual(inv["subcmd"], "article")
        self.assertEqual(inv["args"], ["民法典", "143"])
        self.assertEqual(inv["flags"]["--format"], "json")

    def test_parse_invocation_caps_args_at_three(self):
        parse = self.mod._parse_chinalaw_invocation
        inv = parse("chinalaw articles 民法典 第一条 第二条 第三条 第四条")
        assert inv is not None
        self.assertEqual(len(inv["args"]), 3)

    def test_summarize_tool_result_law_missing(self):
        summarize = self.mod._summarize_tool_result
        payload = json.dumps({"reason": "law_missing", "law_id": None})
        self.assertEqual(summarize(payload), "law_missing")

    def test_summarize_tool_result_article_null(self):
        summarize = self.mod._summarize_tool_result
        payload = json.dumps({"reason": "article_null", "article": None})
        self.assertEqual(summarize(payload), "article_null")

    def test_summarize_tool_result_ok(self):
        summarize = self.mod._summarize_tool_result
        payload = json.dumps({"law": {"id": "x"}, "article": {"number": "1"}})
        self.assertEqual(summarize(payload), "ok")

    def test_summarize_tool_result_block_list(self):
        summarize = self.mod._summarize_tool_result
        # Anthropic tool_result with content as list of blocks
        blocks = [
            {"type": "text", "text": json.dumps({"reason": "law_missing"})}
        ]
        self.assertEqual(summarize(blocks), "law_missing")

    def test_summarize_tool_result_nonjson_md(self):
        summarize = self.mod._summarize_tool_result
        # Markdown output (chinalaw --format md) reads as nonjson but no
        # explicit failure markers — we tag as "nonjson" not "error".
        md = "# 中华人民共和国担保法\n\n- id: x\n- 状态: current"
        self.assertEqual(summarize(md), "nonjson")

    def test_normalize_law_token_strips_nested_brackets(self):
        norm = self.mod._normalize_law_token
        # 〈〉/《》 stripped wherever they appear, but the prefix
        # `中华人民共和国` is only stripped if it's at the *start*; mid-string
        # occurrences inside a longer 司法解释 title stay (substring match
        # still works downstream because the call-side path strips its own
        # leading prefix).
        self.assertEqual(
            norm("最高人民法院关于适用〈中华人民共和国担保法〉若干问题的解释"),
            "最高人民法院关于适用中华人民共和国担保法若干问题的解释",
        )
        # 中华人民共和国 prefix at start IS stripped
        self.assertEqual(norm("中华人民共和国担保法"), "担保法")
        # already short
        self.assertEqual(norm("担保法"), "担保法")
        self.assertEqual(norm(""), "")
        self.assertEqual(norm(None), "")
        # bidirectional substring match works after normalization:
        # cit token contains 担保法; call token IS 担保法 → match.
        cit_norm = norm("最高人民法院关于适用〈中华人民共和国担保法〉若干问题的解释")
        call_norm = norm("中华人民共和国担保法")
        self.assertIn(call_norm, cit_norm)

    def test_annotate_suspected_measurement_substring_match(self):
        # Verifier failure: cited 担保法 with db_no_law.
        # Call chain: model successfully called fetch on 中华人民共和国担保法.
        # Should annotate as suspected_measurement.
        verified = [
            {"law": "担保法", "number": "26", "verified": False, "reason": "db_no_law"},
        ]
        chain = [
            {
                "turn": 1, "subcmd": "fetch",
                "args": ["中华人民共和国担保法"],
                "flags": {}, "result_summary": "nonjson",
            },
        ]
        self.mod._annotate_suspected_measurement(verified, chain)
        self.assertTrue(verified[0]["suspected_measurement"])
        self.assertEqual(verified[0]["evidence_call_index"], 0)

    def test_annotate_suspected_measurement_skips_explicit_failure(self):
        # The chain's call returned `law_missing` — that's evidence the law
        # really isn't in DB, NOT evidence of measurement-class issue.
        verified = [
            {"law": "担保法", "number": "26", "verified": False, "reason": "db_no_law"},
        ]
        chain = [
            {
                "turn": 1, "subcmd": "fetch",
                "args": ["中华人民共和国担保法"],
                "flags": {}, "result_summary": "law_missing",
            },
        ]
        self.mod._annotate_suspected_measurement(verified, chain)
        self.assertFalse(verified[0].get("suspected_measurement", False))

    def test_annotate_suspected_measurement_skips_error_result(self):
        verified = [
            {"law": "担保法", "number": "26", "verified": False, "reason": "db_no_law"},
        ]
        chain = [
            {
                "turn": 1, "subcmd": "fetch",
                "args": ["中华人民共和国担保法"],
                "flags": {}, "result_summary": "error",
            },
        ]
        self.mod._annotate_suspected_measurement(verified, chain)
        self.assertFalse(verified[0].get("suspected_measurement", False))

    def test_annotate_suspected_measurement_skips_search_subcmd(self):
        # `search` args[0] is a keyword phrase, not a law name. Don't index.
        verified = [
            {"law": "担保法", "number": "26", "verified": False, "reason": "db_no_law"},
        ]
        chain = [
            {
                "turn": 1, "subcmd": "search",
                "args": ["保证期间 担保法"],  # keyword phrase
                "flags": {}, "result_summary": "ok",
            },
        ]
        self.mod._annotate_suspected_measurement(verified, chain)
        self.assertFalse(verified[0].get("suspected_measurement", False))

    def test_annotate_suspected_measurement_handles_articles_batch(self):
        # `articles 民法典:524;公司法:32` packs multiple law names.
        verified = [
            {"law": "公司法", "number": "32", "verified": False, "reason": "db_no_law"},
        ]
        chain = [
            {
                "turn": 1, "subcmd": "articles",
                "args": ["民法典:524;公司法:32"],
                "flags": {}, "result_summary": "nonjson",
            },
        ]
        self.mod._annotate_suspected_measurement(verified, chain)
        self.assertTrue(verified[0]["suspected_measurement"])

    def test_annotate_does_not_flag_real_hallucination(self):
        # `db_law_no_article` = strict hallucination (law exists, article
        # number doesn't). Should NEVER be flagged as measurement-class.
        verified = [
            {"law": "民法典", "number": "9999", "verified": False,
             "reason": "db_law_no_article"},
        ]
        chain = [
            {
                "turn": 1, "subcmd": "article",
                "args": ["民法典"], "flags": {}, "result_summary": "ok",
            },
        ]
        self.mod._annotate_suspected_measurement(verified, chain)
        self.assertFalse(verified[0].get("suspected_measurement", False))

    def test_analyze_builds_call_chain_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            qdir = Path(tmp)
            (qdir / "question.txt").write_text("test\tQ\n", encoding="utf-8")
            (qdir / "stderr.log").write_text("exit=0 status=ok\n", encoding="utf-8")
            events = [
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use", "id": "u1", "name": "Bash",
                                "input": {"command": "chinalaw search 担保 --kind article"},
                            }
                        ],
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result", "tool_use_id": "u1",
                                "content": json.dumps({"law": {"id": "x"}}),
                            }
                        ],
                    },
                },
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use", "id": "u2", "name": "Bash",
                                "input": {"command": "chinalaw article 民法典 999"},
                            }
                        ],
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result", "tool_use_id": "u2",
                                "content": json.dumps({"reason": "article_null"}),
                            }
                        ],
                    },
                },
                {"type": "result", "result": "ok"},
            ]
            (qdir / "stream.jsonl").write_text(
                "\n".join(json.dumps(ev, ensure_ascii=False) for ev in events) + "\n",
                encoding="utf-8",
            )

            original_verify = self.mod._verify_citations
            self.mod._verify_citations = lambda citations: []
            try:
                result = self.mod.analyze(qdir)
            finally:
                self.mod._verify_citations = original_verify

            chain = result["chinalaw_call_chain"]
            self.assertEqual(len(chain), 2)
            self.assertEqual(chain[0]["subcmd"], "search")
            self.assertEqual(chain[0]["result_summary"], "ok")
            self.assertEqual(chain[1]["subcmd"], "article")
            self.assertEqual(chain[1]["args"], ["民法典", "999"])
            self.assertEqual(chain[1]["result_summary"], "article_null")
            # turn counter increments on each assistant event
            self.assertLess(chain[0]["turn"], chain[1]["turn"])


if __name__ == "__main__":
    unittest.main()
