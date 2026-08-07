"""LightRAG 语义知识检索器。

将 domain_pack 的标签转为自然语言查询，通过 LightRAG 在论文知识库中
检索相关段落。作为 domain_pack 现有标签检索的语义补充，不替代任何现有逻辑。

设计原则:
  - 同步接口：所有调用者都是 sync 函数，内部通过进程级事件循环驱动 LightRAG
  - 静默降级：LightRAG 不可用（数据未建好、Ollama 未启动）时返回 None
  - 标签复用：直接使用 domain_pack 已有的 tag_descriptions 构造查询语句
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import threading
from typing import Any

try:
    from lightrag import LightRAG, QueryParam
except ImportError:  # pragma: no cover - depends on optional local knowledge stack
    LightRAG = None
    QueryParam = None

try:
    from knowledge.src.config import (
        DEEPSEEK_MODEL,
        create_embedding_func,
        create_llm_func,
    )
except ImportError:  # pragma: no cover - depends on optional local knowledge stack
    DEEPSEEK_MODEL = ""
    create_embedding_func = None
    create_llm_func = None

# 知识库数据目录（相对于项目根目录 FJSP-AGENT/）
_DEFAULT_WORKING_DIR = "./knowledge/fjsp_kb"

# 单次查询返回的最大段落长度（字符数），避免撑爆 context
_MAX_CONTENT_CHARS = 8000
_RESPONSE_TYPE = "Actionable implementation knowledge card"
_RAG_EVENT_LOOP: asyncio.AbstractEventLoop | None = None
_RAG_EVENT_LOOP_LOCK = threading.Lock()
_RAG_QUERY_LOCK = threading.Lock()


def _rag_event_loop() -> asyncio.AbstractEventLoop:
    """Return the process-local LightRAG loop.

    LightRAG caches asyncio locks by storage namespace. Those locks are bound to
    the loop that first creates them, so all sync FJSP retrieval calls in one
    process must reuse the same loop instead of creating a fresh loop per query.
    """

    global _RAG_EVENT_LOOP
    with _RAG_EVENT_LOOP_LOCK:
        if _RAG_EVENT_LOOP is None or _RAG_EVENT_LOOP.is_closed():
            _RAG_EVENT_LOOP = asyncio.new_event_loop()
        return _RAG_EVENT_LOOP


def _run_lightrag_query(
    query: str,
    working_dir: str,
    mode: str = "mix",
    top_k: int = 10,
    only_need_context: bool = False,
    response_type: str = _RESPONSE_TYPE,
) -> str | None:
    """在进程级 LightRAG 事件循环中执行异步查询。

    LightRAG 的 storages 初始化与查询都是异步的。由于调用方都在 sync 上下文中，
    这里用同一个常驻事件循环来驱动整个流程，避免 LightRAG 内部按 namespace
    缓存的 asyncio.Lock 被绑定到不同 event loop。
    """
    if (
        LightRAG is None
        or QueryParam is None
        or create_embedding_func is None
        or create_llm_func is None
    ):
        return None

    async def _run():
        rag = LightRAG(
            working_dir=working_dir,
            embedding_func=create_embedding_func(),
            llm_model_func=create_llm_func(),
            llm_model_name=DEEPSEEK_MODEL,
        )
        await rag.initialize_storages()

        result = await rag.aquery(
            query,
            param=QueryParam(
                mode=mode,
                only_need_context=only_need_context,
                response_type=response_type,
                top_k=top_k,
                stream=False,
            ),
        )
        if result is None:
            return None
        if isinstance(result, str):
            return result.strip() or None
        # QueryResult 对象
        content = getattr(result, "content", None)
        return str(content).strip() if content else None

    loop = _rag_event_loop()
    try:
        with _RAG_QUERY_LOCK:
            return loop.run_until_complete(_run())
    except Exception:
        # 任何异常（Ollama 不可达、数据文件不存在等）都静默降级
        return None


def _build_query_from_tags(
    tags: list[str],
    tag_descriptions: dict[str, str] | None,
    query_template: str | None = None,
) -> str:
    """将标签列表拼接成自然语言查询语句。

    优先使用 domain_pack 的 tag_descriptions（中文领域术语解释），
    没有描述时回退到标签名本身的文本形式。
    """
    parts: list[str] = []
    seen: set[str] = set()

    for tag in tags:
        tag = tag.strip().lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)

        desc = (tag_descriptions or {}).get(tag, "")
        if desc:
            parts.append(desc)
        else:
            # 没有描述时把下划线转空格，当短语用
            parts.append(tag.replace("_", " "))

        if len(parts) >= 8:
            break

    if not parts:
        return "柔性作业车间调度（FJSP）问题的算法设计与约束处理方法"

    descriptions = "；".join(parts)
    if query_template:
        try:
            return query_template.format(
                tags=", ".join(tags[:8]),
                tag_descriptions=descriptions,
            )
        except (KeyError, IndexError, ValueError):
            pass
    return (
        f"在柔性作业车间调度（FJSP）问题中，"
        f"请先检索以下方法或概念的论文证据，再基于证据整理成可执行知识卡："
        f"{descriptions}。"
        f"输出必须面向 Coding Agent，包含问题语义、算法模式、解码/插入/局部搜索实现要点、"
        f"合法性检查、容易出错的坑，以及可直接转化为代码的 checklist。"
    )


def build_semantic_query(
    tags: list[str],
    tag_descriptions: dict[str, str] | None = None,
    query_template: str | None = None,
) -> str:
    """Expose the exact query builder so RAG card caching can hash the topic before retrieval."""

    return _build_query_from_tags(tags, tag_descriptions, query_template=query_template)


def retrieve_semantic_knowledge(
    tags: list[str],
    tag_descriptions: dict[str, str] | None = None,
    working_dir: str | Path = _DEFAULT_WORKING_DIR,
    max_content_chars: int = _MAX_CONTENT_CHARS,
    mode: str = "mix",
    top_k: int = 10,
    query_template: str | None = None,
    only_need_context: bool = False,
    response_type: str = _RESPONSE_TYPE,
) -> dict[str, Any] | None:
    """将 domain_pack 标签转为自然语言查询，通过 LightRAG 检索论文段落。

    这是与 domain_pack 现有标签检索并行的语义知识源。调用方将返回结果
    追加到 context_packet 或 worker context 中，与 tagged_cards 的知识
    一同呈现给 Agent。

    Args:
        tags: domain_pack 的知识标签列表（如 ["tabu_search", "memetic"]）
        tag_descriptions: domain_pack.knowledge_query_tag_descriptions，
                          每个标签的中文解释
        working_dir: LightRAG 知识库数据目录
        max_content_chars: 返回内容的最大字符数

    Returns:
        {"source": "lightrag", "query": str, "content": str, "tags_used": [...]}
        或 None（LightRAG 不可用时）
    """
    if not tags:
        return None

    query = _build_query_from_tags(tags, tag_descriptions, query_template=query_template)
    result_text = _run_lightrag_query(
        query,
        str(working_dir),
        mode=mode,
        top_k=top_k,
        only_need_context=only_need_context,
        response_type=response_type,
    )

    if not result_text:
        return None

    return {
        "source": "lightrag",
        "query": query,
        "content": result_text[:max_content_chars],
        "tags_used": list(tags[:8]),
        "only_need_context": only_need_context,
        "response_type": response_type,
    }
