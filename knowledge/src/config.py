"""LightRAG 配置工厂 — Embedding 与 LLM 函数的唯一创建入口。

本模块是知识库所有组件（建库脚本、检索器、API 服务）共享的配置来源。
修改 Embedding 后端或 LLM 模型时只需改这一个文件。
"""
from __future__ import annotations

import os
from functools import partial
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from lightrag.llm.ollama import ollama_embed
from lightrag.llm.openai import openai_complete_if_cache
from lightrag.utils import EmbeddingFunc


# ============================================================
# 环境变量 fallback — 优先从进程环境读取，否则用 .env 中的值
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip() or default


def _read_env_file(key: str) -> str:
    path = os.getenv(f"{key}_FILE", "").strip()
    return Path(path).read_text(encoding="utf-8").strip() if path else ""


def _first_env(keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        value = os.getenv(key, "").strip()
        if value:
            return value
        file_value = _read_env_file(key)
        if file_value:
            return file_value
    return default


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key, "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key, "").strip()
    return int(raw) if raw else default


def _reasoning_effort(value: str) -> str:
    normalized = value.strip().lower()
    return normalized if normalized in {"low", "medium", "high"} else ""


def _openai_base_url(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    return base_url if base_url.endswith("/v1") else f"{base_url}/v1"


DEEPSEEK_API_KEY = _first_env(
    ("DEEPSEEK_API_KEY", "LLM_BINDING_API_KEY", "OPENAI_API_KEY"),
    "sk-644b222f74b14335bc6859dc7555d566",
)
DEEPSEEK_BASE_URL = _first_env(
    ("DEEPSEEK_BASE_URL", "LLM_BINDING_HOST", "OPENAI_BASE_URL"),
    "https://api.deepseek.com",
).rstrip("/")
DEEPSEEK_MODEL = _first_env(
    ("DEEPSEEK_MODEL", "LLM_MODEL", "OPENAI_MODEL"),
    "deepseek-v4-pro",
)
LLM_REASONING_EFFORT = _reasoning_effort(
    _first_env(("LLM_REASONING_EFFORT", "OPENAI_REASONING_EFFORT"), "")
)

VLM_PROCESS_ENABLE = _env_bool("VLM_PROCESS_ENABLE", True)
VLM_API_KEY = _first_env(
    (
        "VLM_LLM_BINDING_API_KEY",
        "VLM_API_KEY",
        "LLM_BINDING_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
    ),
    DEEPSEEK_API_KEY,
)
VLM_BASE_URL = _first_env(
    (
        "VLM_LLM_BINDING_HOST",
        "VLM_BASE_URL",
        "LLM_BINDING_HOST",
        "DEEPSEEK_BASE_URL",
        "OPENAI_BASE_URL",
    ),
    DEEPSEEK_BASE_URL,
).rstrip("/")
VLM_MODEL = _first_env(
    ("VLM_LLM_MODEL", "VLM_MODEL", "LLM_MODEL", "DEEPSEEK_MODEL", "OPENAI_MODEL"),
    DEEPSEEK_MODEL,
)
VLM_REASONING_EFFORT = _reasoning_effort(
    _first_env(
        (
            "VLM_REASONING_EFFORT",
            "VLM_LLM_REASONING_EFFORT",
            "LLM_REASONING_EFFORT",
            "OPENAI_REASONING_EFFORT",
        ),
        LLM_REASONING_EFFORT,
    )
)
VLM_MAX_ASYNC = _env_int("VLM_MAX_ASYNC_LLM", 2)
VLM_TIMEOUT = _env_int("VLM_LLM_TIMEOUT", 240)


# ============================================================
# Embedding 配置
# ============================================================

def create_embedding_func() -> EmbeddingFunc:
    """创建 Ollama 本地 Embedding 函数。

    当前使用 nomic-embed-text (768 维)，中文与英文均适用。
    """
    return EmbeddingFunc(
        embedding_dim=768,
        max_token_size=8192,
        model_name="nomic-embed-text",
        func=partial(
            ollama_embed.func,
            embed_model="nomic-embed-text",
            host="http://localhost:11434",
        ),
    )


# ============================================================
# LLM 配置
# ============================================================

def _create_openai_compatible_func(
    *,
    model: str,
    base_url: str,
    api_key: str,
    reasoning_effort: str = "",
) -> Callable[..., Any]:
    """创建 OpenAI-compatible LLM/VLM 函数。

    LightRAG 要求 llm_model_func 的签名为
        (prompt, system_prompt=None, history_messages=[], **kwargs) -> str

    我们在 wrapper 内部绑定 model、base_url、api_key，
    然后转发给 openai_complete_if_cache。
    """

    async def _llm_wrapper(
        prompt: str,
        system_prompt: str | None = None,
        history_messages: list | None = None,
        **kwargs,
    ) -> str:
        if reasoning_effort:
            kwargs.setdefault("reasoning_effort", reasoning_effort)
        return await openai_complete_if_cache(
            model,
            prompt,
            system_prompt=system_prompt,
            history_messages=history_messages or [],
            base_url=_openai_base_url(base_url),
            api_key=api_key,
            **kwargs,
        )

    return _llm_wrapper


def create_llm_func() -> Callable[..., Any]:
    """创建 DeepSeek 文本 LLM 函数。"""
    return _create_openai_compatible_func(
        model=DEEPSEEK_MODEL,
        base_url=DEEPSEEK_BASE_URL,
        api_key=DEEPSEEK_API_KEY,
        reasoning_effort=LLM_REASONING_EFFORT,
    )


def create_vlm_func() -> Callable[..., Any]:
    """创建用于 LightRAG 多模态分析的 VLM 函数。

    VLM 角色会收到 image_inputs；底层 openai_complete_if_cache 已支持
    OpenAI-compatible 的图片输入消息格式。
    """
    return _create_openai_compatible_func(
        model=VLM_MODEL,
        base_url=VLM_BASE_URL,
        api_key=VLM_API_KEY,
        reasoning_effort=VLM_REASONING_EFFORT,
    )


# ============================================================
# 知识库实例工厂
# ============================================================

async def create_lightrag(
    working_dir: str | Path = "./knowledge/fjsp_kb",
) -> "LightRAG":
    """创建并初始化 LightRAG 实例。

    调用 initialize_storages() 是必需的 —— 它会初始化 pipeline_status
    命名空间，ainsert() 依赖于此。

    Args:
        working_dir: 知识库数据存储目录，默认 knowledge/fjsp_kb。
                     路径相对于项目根目录（FJSP-AGENT/）。

    Returns:
        已配置并初始化完成的 LightRAG 实例
    """
    from lightrag import LightRAG, RoleLLMConfig

    rag = LightRAG(
        working_dir=str(working_dir),
        embedding_func=create_embedding_func(),
        llm_model_func=create_llm_func(),
        llm_model_name=DEEPSEEK_MODEL,
        vlm_process_enable=VLM_PROCESS_ENABLE,
        role_llm_configs={
            "vlm": RoleLLMConfig(
                func=create_vlm_func(),
                max_async=VLM_MAX_ASYNC,
                timeout=VLM_TIMEOUT,
                metadata={
                    "binding": "openai",
                    "model": VLM_MODEL,
                    "host": _openai_base_url(VLM_BASE_URL),
                    "api_key": VLM_API_KEY,
                    "reasoning_effort": VLM_REASONING_EFFORT,
                    "is_cross_provider": VLM_MODEL != DEEPSEEK_MODEL
                    or VLM_BASE_URL.rstrip("/") != DEEPSEEK_BASE_URL.rstrip("/"),
                },
            )
        },
    )
    await rag.initialize_storages()
    return rag
