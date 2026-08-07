from __future__ import annotations

import io
import json
import os
import unittest
from unittest.mock import patch

from harness_agent.deepseek_client import DeepSeekClient, DeepSeekConfig


class DeepSeekClientTests(unittest.TestCase):
    def test_chat_uses_a_gateway_compatible_user_agent(self) -> None:
        raw = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

        with patch("urllib.request.urlopen", return_value=_FakeResponse(raw)) as urlopen:
            client = DeepSeekClient(DeepSeekConfig(api_key="test-key"))
            client.chat([{"role": "user", "content": "ping"}])

        request = urlopen.call_args.args[0]
        self.assertEqual("fjsp-harness-agent/1.0", request.get_header("User-agent"))

    def test_chat_with_usage_preserves_prompt_cache_metrics(self) -> None:
        raw = {
            "choices": [{"message": {"role": "assistant", "content": '{"summary":"ok"}'}}],
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 120,
                "total_tokens": 1120,
                "prompt_cache_hit_tokens": 640,
                "prompt_cache_miss_tokens": 360,
            },
        }

        with patch("urllib.request.urlopen", return_value=_FakeResponse(raw)):
            client = DeepSeekClient(DeepSeekConfig(api_key="test-key"))
            result = client.chat_with_usage([{"role": "user", "content": "return json"}], json_mode=True)

        self.assertEqual('{"summary":"ok"}', result.content)
        self.assertEqual(640, result.usage["prompt_cache_hit_tokens"])
        self.assertEqual(0.64, result.cache_hit_ratio)

    def test_chat_falls_back_to_reasoning_content_when_content_is_empty(self) -> None:
        raw = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": '{"summary":"ok","changes":[]}',
                    }
                }
            ]
        }

        with patch("urllib.request.urlopen", return_value=_FakeResponse(raw)):
            client = DeepSeekClient(DeepSeekConfig(api_key="test-key"))
            content = client.chat([{"role": "user", "content": "return json"}], json_mode=True)

        self.assertEqual('{"summary":"ok","changes":[]}', content)

    def test_from_env_honors_timeout_override(self) -> None:
        env = {
            "DEEPSEEK_API_KEY": "test-key",
            "DEEPSEEK_TIMEOUT_SECONDS": "9",
        }
        with patch.dict(os.environ, env, clear=False):
            client = DeepSeekClient.from_env()

        self.assertEqual(9, client.config.timeout_seconds)


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._buffer = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._buffer.read()


if __name__ == "__main__":
    unittest.main()
