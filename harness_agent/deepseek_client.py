from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class DeepSeekUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class DeepSeekConfig:
    api_key: str
    model: str = "deepseek-v4-pro"
    base_url: str = "https://api.deepseek.com"
    timeout_seconds: int = 120


class DeepSeekClient:
    def __init__(self, config: DeepSeekConfig) -> None:
        self.config = config

    @staticmethod
    def from_env(
        api_key_env: str = "DEEPSEEK_API_KEY",
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int = 120,
    ) -> "DeepSeekClient":
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise DeepSeekUnavailable(f"environment variable {api_key_env} is not set")
        return DeepSeekClient(
            DeepSeekConfig(
                api_key=api_key,
                model=model or os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro"),
                base_url=base_url or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                timeout_seconds=timeout_seconds,
            )
        )

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        url = self.config.base_url.rstrip("/") + "/chat/completions"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"DeepSeek HTTP {exc.code}: {body[:1000]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"DeepSeek request failed: {exc}") from exc

        data = json.loads(raw)
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"DeepSeek response has no choices: {raw[:1000]}")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError(f"DeepSeek response has empty content: {raw[:1000]}")
        return content.strip()
