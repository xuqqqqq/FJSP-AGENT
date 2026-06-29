from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from harness_agent.deepseek_client import DeepSeekClient, DeepSeekConfig


class DeepSeekClientTests(unittest.TestCase):
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
