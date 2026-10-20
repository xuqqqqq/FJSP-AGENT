from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from harness_agent.model_catalog import _CACHE, discover_model_catalog


class ModelCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        _CACHE.clear()

    def response(self, data: dict) -> io.BytesIO:
        return io.BytesIO(json.dumps(data).encode())

    def test_discovers_actual_models_and_context_limits_without_legacy_entries(self) -> None:
        with patch("harness_agent.model_catalog.urllib.request.urlopen", return_value=self.response({"data": [
            {"id": "dsv4-0731", "max_model_len": 262144},
            {"id": "dsv41-1017", "max_model_len": 1048576},
            {"id": "dsv4-0731", "max_model_len": 262144},
        ]})) as request:
            result = discover_model_catalog("qiming", "http://gateway.test/v1/", "private-secret")
        self.assertEqual([
            {"id": "qiming/dsv4-0731", "context_limit": 262144},
            {"id": "qiming/dsv41-1017", "context_limit": 1048576},
        ], result["models"])
        self.assertEqual("http://gateway.test/v1/models", request.call_args.args[0].full_url)
        self.assertNotIn("private-secret", json.dumps(result))

    def test_cache_expires_and_does_not_retain_removed_models(self) -> None:
        with patch("harness_agent.model_catalog.time.monotonic", side_effect=[100, 101, 161]), patch(
            "harness_agent.model_catalog.urllib.request.urlopen", side_effect=[
                self.response({"data": [{"id": "old"}]}),
                self.response({"data": [{"id": "new"}]}),
            ],
        ) as request:
            discover_model_catalog("qiming", "http://gateway.test/v1", "key")
            discover_model_catalog("qiming", "http://gateway.test/v1", "key")
            result = discover_model_catalog("qiming", "http://gateway.test/v1", "key")
        self.assertEqual(2, request.call_count)
        self.assertEqual([{"id": "qiming/new"}], result["models"])

    def test_rotated_credentials_do_not_reuse_the_previous_catalog(self) -> None:
        with patch("harness_agent.model_catalog.urllib.request.urlopen", side_effect=[
            self.response({"data": [{"id": "first"}]}),
            self.response({"data": [{"id": "second"}]}),
        ]) as request:
            discover_model_catalog("qiming", "http://gateway.test/v1", "key-a")
            result = discover_model_catalog("qiming", "http://gateway.test/v1", "key-b")
        self.assertEqual(2, request.call_count)
        self.assertEqual([{"id": "qiming/second"}], result["models"])

    def test_http_failure_does_not_invent_or_restore_models(self) -> None:
        with patch("harness_agent.model_catalog.urllib.request.urlopen", side_effect=urllib.error.HTTPError(
            "http://gateway.test/v1/models", 401, "Unauthorized", {}, None
        )):
            result = discover_model_catalog("qiming", "http://gateway.test/v1", "private-secret")
        self.assertEqual({"models": [], "error": "HTTP 401"}, result)

    def test_invalid_response_does_not_supply_static_fallbacks(self) -> None:
        with patch("harness_agent.model_catalog.urllib.request.urlopen", return_value=self.response({"data": "invalid"})):
            result = discover_model_catalog("qiming", "http://gateway.test/v1", "key")
        self.assertEqual([], result["models"])
        self.assertEqual("ValueError", result["error"])


if __name__ == "__main__":
    unittest.main()
