"""DeepSeek API 薄客户端，统一配置、用量和缓存命中信息。"""

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
    """本地没有可用密钥或 provider 配置时抛出的明确异常。"""

    pass


@dataclass(frozen=True)
class DeepSeekConfig:
    """一次 DeepSeek 客户端使用的已解析配置。

    密钥只保存在内存中，不会写入 Context Packet、Worker worktree 或
    Web 状态文件。
    """

    api_key: str
    model: str = "deepseek-v4-pro"
    base_url: str = "https://api.deepseek.com"
    timeout_seconds: int = 120


@dataclass(frozen=True)
class DeepSeekChatResult:
    """模型正文与计费统计；缓存命中率由 usage 字段统一换算。"""

    content: str
    usage: dict[str, int]

    @property
    def cache_hit_ratio(self) -> float | None:
        """兼容不同 provider usage 字段，返回 0 到 1 的缓存命中比例。"""

        hit = int(self.usage.get("prompt_cache_hit_tokens") or self.usage.get("cached_tokens") or 0)
        miss = int(self.usage.get("prompt_cache_miss_tokens") or 0)
        denominator = hit + miss
        if denominator <= 0:
            denominator = int(self.usage.get("prompt_tokens") or 0)
        if denominator <= 0:
            return None
        return round(hit / denominator, 6)


class DeepSeekClient:
    """OpenAI 兼容 `/chat/completions` 接口的轻量同步客户端。

    本类只负责配置、HTTP、JSON 和用量统计，不包含 Main Agent、Coding
    Agent 或语义审查的业务提示词；角色提示词由各自模块维护。
    """

    def __init__(self, config: DeepSeekConfig) -> None:
        self.config = config

    @staticmethod
    def from_env(
        api_key_env: str = "DEEPSEEK_API_KEY",
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int = 120,
    ) -> "DeepSeekClient":
        """按优先级读取本地 env/密钥文件并构建客户端。"""

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
        """只需要正文时使用的便捷接口。"""

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
        """发送一次非流式请求，并保留 token/cache 使用信息。

        `json_mode` 只要求 provider 返回 JSON 对象；调用方仍需做 schema
        归一化和证据校验，不能直接信任模型输出。
        """

        # 这里保持 OpenAI 兼容载荷，便于通过 base_url 切换兼容 provider。
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
                "User-Agent": "fjsp-harness-agent/1.0",
            },
            method="POST",
        )
        # HTTP 错误保留响应体摘要，方便 Web/同轮修补区分余额、鉴权和格式问题。
        try:
            raw = urlopen_read_with_deadline(request, timeout_seconds=self.config.timeout_seconds)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"DeepSeek HTTP {exc.code}: {body[:1000]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"DeepSeek request failed: {exc}") from exc

        # provider 有时只返回 reasoning_content，因此正文读取提供一次兼容回退。
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
    """读取最常见的 KEY=VALUE 子集，且不覆盖已存在的进程环境变量。"""

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
    """优先读取直接环境变量，其次读取私有密钥文件。"""

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
    """把外部超时限制在 5 到 600 秒，避免配置错误造成无限等待。"""

    if raw_value:
        try:
            value = int(raw_value)
        except ValueError:
            value = default
    else:
        value = default
    return max(5, min(600, value))


def urlopen_read_with_deadline(request: urllib.request.Request, *, timeout_seconds: int) -> str:
    """为 urllib 再加一层 Future deadline，处理底层 socket 未及时退出的情况。"""

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
    """兼容历史 UI 中使用过的模型别名。"""

    aliases = {
        "deepseek-4-pro": "deepseek-v4-pro",
        "deepseek-4-flash": "deepseek-v4-flash",
    }
    return aliases.get(model, model)
