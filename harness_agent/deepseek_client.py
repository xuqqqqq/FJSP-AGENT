from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class DeepSeekUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class DeepSeekConfig:
    api_key: str
    model: str = "deepseek-v4-pro"
    base_url: str = "https://api.deepseek.com"
    timeout_seconds: int = 120


@dataclass(frozen=True)
class DeepSeekChatResult:
    content: str
    usage: dict[str, int]

    @property
    def cache_hit_ratio(self) -> float | None:
        hit = int(self.usage.get("prompt_cache_hit_tokens") or self.usage.get("cached_tokens") or 0)
        miss = int(self.usage.get("prompt_cache_miss_tokens") or 0)
        denominator = hit + miss
        if denominator <= 0:
            denominator = int(self.usage.get("prompt_tokens") or 0)
        if denominator <= 0:
            return None
        return round(hit / denominator, 6)


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
        load_local_env()
        api_key = resolve_secret(api_key_env, file_env=f"{api_key_env}_FILE")
        if not api_key:
            raise DeepSeekUnavailable(f"environment variable {api_key_env} is not set")
        timeout = resolve_timeout_seconds(os.environ.get("DEEPSEEK_TIMEOUT_SECONDS"), default=timeout_seconds)
        return DeepSeekClient(
            DeepSeekConfig(
                api_key=api_key,
                model=normalize_deepseek_model(model or os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")),
                base_url=base_url or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                timeout_seconds=timeout,
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
        return self.chat_with_usage(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        ).content

    def chat_with_usage(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> DeepSeekChatResult:
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
            raw = urlopen_read_with_deadline(request, timeout_seconds=self.config.timeout_seconds)
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
            content = message.get("reasoning_content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError(f"DeepSeek response has empty content: {raw[:1000]}")
        usage_raw = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        usage = {
            str(key): int(value)
            for key, value in usage_raw.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        return DeepSeekChatResult(content=content.strip(), usage=usage)


def is_deepseek_configured(api_key_env: str = "DEEPSEEK_API_KEY") -> bool:
    """Return whether a DeepSeek key is available without exposing the key."""

    load_local_env()
    return bool(resolve_secret(api_key_env, file_env=f"{api_key_env}_FILE"))


def load_local_env() -> None:
    """Load optional local environment files ignored by git.

    The harness should be runnable as an independent project, but API keys must
    never be committed.  This tiny parser intentionally supports only the common
    `KEY=VALUE` subset used by `.env` files and never overwrites values already
    present in the process environment.
    """

    for path in local_env_candidates():
        if path.is_file():
            load_env_file(path)


def local_env_candidates() -> list[Path]:
    """Return the local env files checked by load_local_env, in load order."""

    explicit = os.environ.get("FJSP_AGENT_ENV_FILE")
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))

    repo_root = Path(__file__).resolve().parents[1]
    for candidate in (Path.cwd() / ".env", Path.cwd() / ".env.local", repo_root / ".env", repo_root / ".env.local"):
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def load_env_file(path: Path) -> None:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lstrip("\ufeff")
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def resolve_secret(value_env: str, *, file_env: str) -> str | None:
    value = os.environ.get(value_env)
    if value:
        return value.strip()
    file_path = os.environ.get(file_env)
    if not file_path:
        return None
    path = Path(file_path)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip() or None


def resolve_timeout_seconds(raw_value: str | None, *, default: int) -> int:
    if raw_value:
        try:
            value = int(raw_value)
        except ValueError:
            value = default
    else:
        value = default
    return max(5, min(600, value))


def urlopen_read_with_deadline(request: urllib.request.Request, *, timeout_seconds: int) -> str:
    timeout = resolve_timeout_seconds(str(timeout_seconds), default=120)

    def read_response() -> str:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8")

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(read_response)
    try:
        return future.result(timeout=timeout)
    except FutureTimeoutError as exc:
        future.cancel()
        raise RuntimeError(f"DeepSeek request timed out after {timeout} seconds") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def normalize_deepseek_model(model: str) -> str:
    aliases = {
        "deepseek-4-pro": "deepseek-v4-pro",
        "deepseek-4-flash": "deepseek-v4-flash",
    }
    return aliases.get(model, model)
