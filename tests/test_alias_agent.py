from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from chinalaw.alias_agent import AliasAgentRecoverableError, derive_aliases


def _fake_post(_url: str, _headers: dict, _body: dict, _timeout: float) -> str:
    return json.dumps(
        {"choices": [{"message": {"content": '["示例法", "示例规定"]'}}]},
        ensure_ascii=False,
    )


class AliasAgentBoundaryTests(unittest.TestCase):
    def _kwargs(self) -> dict:
        return {
            "base_url": "https://api.example.test/v1",
            "api_key": "secret",
            "model": "example-model",
            "http_post": _fake_post,
        }

    def test_invalid_numeric_environment_is_recoverable(self) -> None:
        with patch.dict(
            os.environ,
            {"CHINALAW_ALIAS_AGENT_MAX": "many"},
            clear=False,
        ), self.assertRaises(AliasAgentRecoverableError) as caught:
            derive_aliases("中华人民共和国示例法", **self._kwargs())
        self.assertEqual(caught.exception.reason, "invalid_config")

        with patch.dict(
            os.environ,
            {"CHINALAW_ALIAS_AGENT_TIMEOUT": "forever"},
            clear=False,
        ), self.assertRaises(AliasAgentRecoverableError) as caught:
            derive_aliases("中华人民共和国示例法", **self._kwargs())
        self.assertEqual(caught.exception.reason, "invalid_config")

    def test_non_https_or_local_endpoint_is_recoverable(self) -> None:
        for base_url in ("http://api.example.test/v1", "https://127.0.0.1/v1"):
            with self.subTest(base_url=base_url), self.assertRaises(
                AliasAgentRecoverableError
            ) as caught:
                derive_aliases(
                    "中华人民共和国示例法",
                    **{**self._kwargs(), "base_url": base_url},
                )
            self.assertEqual(caught.exception.reason, "invalid_config")

    def test_malformed_provider_shapes_are_recoverable(self) -> None:
        bodies = (
            "[]",
            '{"choices":"bad"}',
            '{"choices":[1]}',
            '{"choices":[{"message":"bad"}]}',
        )
        for body in bodies:
            with self.subTest(body=body):
                with self.assertRaises(AliasAgentRecoverableError) as caught:
                    derive_aliases(
                        "中华人民共和国示例法",
                        **{
                            **self._kwargs(),
                            "http_post": lambda *_args, body=body: body,
                        },
                    )
                self.assertEqual(caught.exception.reason, "invalid_response")


if __name__ == "__main__":
    unittest.main()
