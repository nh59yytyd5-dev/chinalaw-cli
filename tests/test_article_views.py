"""Presentation must not change source selection, evidence, or error semantics."""

import copy
import unittest
from unittest.mock import patch

from chinalaw import mcp
from chinalaw.article_views import article_view


class ArticleViewTests(unittest.TestCase):
    def test_compact_preserves_distinct_selected_current_and_all_unlisted_fields(self):
        article = {"number": "1", "text": "完整正文。\n第二段。", "source_url": "https://example.test/old"}
        old = {"id": "old", "effective_at": "2020-01-01"}
        new = {"id": "new", "effective_at": "2025-01-01"}
        payload = {
            "article": article, "item": article, "requested_number": "第一条",
            "law": {"id": "test", "revisions": [new, old], "work_versions": [old, new],
                    "revision_count": 2, "selected_revision": old, "current_revision": new,
                    "warnings": [{"code": "retain-me"}], "unknown_future_field": "retain-me"},
            "diagnostic": {"code": "retain-me"},
        }
        original = copy.deepcopy(payload)
        compact = article_view(payload, law="原始名称", number="第一条", as_of="2021-01-01",
                               detail="compact")
        self.assertEqual(payload, original)
        self.assertEqual(article_view(payload, law="原始名称", number="第一条"), original)
        self.assertEqual(compact["article"], article)
        self.assertEqual(compact["law"]["selected_revision"], old)
        self.assertEqual(compact["law"]["current_revision"], new)
        self.assertEqual(compact["view"]["omitted_fields"],
                         ["item", "law.revisions", "law.work_versions"])
        self.assertEqual(compact["view"]["full"], {
            "tool": "chinalaw_article",
            "arguments": {"law": "原始名称", "number": "第一条", "as_of": "2021-01-01", "detail": "full"},
        })
        # Restoring only the documented omitted fields exactly reconstructs the original.
        compact.pop("view")
        compact["item"] = original["item"]
        for key in ["revisions", "work_versions"]:
            compact["law"][key] = original["law"][key]
        self.assertEqual(compact, original)

    def test_missing_and_nonidentical_items_are_never_dropped(self):
        for payload in [
            {"article": None, "item": None, "found": False, "error": "article_not_found"},
            {"law": "unknown", "found": False, "diagnosis": {"reason": "law_missing"}},
            {"article": {"text": "A"}, "item": {"text": "B"}},
        ]:
            with self.subTest(payload=payload):
                compact = article_view(payload, law="test", number="1", detail="compact")
                self.assertEqual(compact.pop("view")["omitted_fields"], [])
                self.assertEqual(compact, payload)

    def test_stdio_rejects_unknown_detail_before_calling_service(self):
        def forbidden(*args, **kwargs):
            self.fail("Invalid view must be rejected before query execution")

        with patch("chinalaw.mcp.service.get_article", forbidden):
            result = mcp.handle_request({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "chinalaw_article", "arguments": {
                    "law": "test", "number": "1", "detail": "invalid",
                }},
            })
        self.assertEqual(result["error"]["code"], -32602)
        self.assertIn("detail", result["error"]["message"])


if __name__ == "__main__":
    unittest.main()
