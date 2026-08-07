"""FJSP 知识库建库脚本。

面向变种 8（机器可用性 / NFA-FJSP）论文资料，只接受 MinerU 处理后的
论文目录作为知识库输入：

    PDF staging -> pending_parse -> mineru -> i/t/e 多模态分析 -> P 语义分块
    -> 实体关系抽取 -> 图与向量库写入

用法（在 FJSP-AGENT 项目根目录执行）:

    # 预览多模态建库计划
    uv run python -m knowledge.src.build_kb --dry-run

    # 只导入前 3 篇（测试）
    uv run python -m knowledge.src.build_kb --limit 3

    # 全量多模态导入
    uv run python -m knowledge.src.build_kb
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from lightrag.constants import FULL_DOCS_FORMAT_PENDING_PARSE
from lightrag.parser.external.mineru.cache import (
    MinerUParserOptions,
    compute_size_and_hash,
    raw_dir_for_parsed_dir,
)
from lightrag.parser.external.mineru.manifest import Manifest, ManifestFile, write_manifest
from lightrag.utils_pipeline import normalize_document_file_path, parsed_artifact_dir_for

from knowledge.src.config import VLM_MODEL, VLM_PROCESS_ENABLE, create_lightrag


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "ALL-Input-Information" / "8-nfa-FJSP"
DEFAULT_WORKING_DIR = PROJECT_ROOT / "knowledge" / "fjsp_kb"
DEFAULT_STAGING_DIR = PROJECT_ROOT / "inputs"
DEFAULT_PARSE_ENGINE = "mineru"
DEFAULT_PROCESS_OPTIONS = "P"


@dataclass(frozen=True)
class MinerUPaper:
    paper_dir: Path
    source_pdf: Path
    staged_name: str
    content_list: Path

    @property
    def title(self) -> str:
        title = self.paper_dir.name
        marker = ".pdf-"
        if marker in title:
            title = title.split(marker, 1)[0]
        return title.removesuffix(".pdf").strip() or self.source_pdf.stem


# ============================================================
# MinerU 文件收集
# ============================================================

def collect_mineru_papers(input_dir: Path) -> list[MinerUPaper]:
    """收集 MinerU 论文目录。

    输入目录必须同时包含 *_origin.pdf 和 *_content_list.json；images/、
    full.md、layout.json 等作为 MinerU 附件被一并复用。
    """
    paper_dirs = sorted({path.parent for path in input_dir.rglob("*_content_list.json")})
    papers: list[MinerUPaper] = []
    seen_titles: set[str] = set()

    for paper_dir in paper_dirs:
        source_pdf = _select_source_pdf(paper_dir)
        if source_pdf is None:
            continue
        content_list = _select_content_list(paper_dir)
        if content_list is None:
            continue

        title = _paper_title(paper_dir, source_pdf)
        title_key = _normalize_title_key(title)
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)

        staged_name = _staged_pdf_name(title, source_pdf, input_dir)
        papers.append(
            MinerUPaper(
                paper_dir=paper_dir,
                source_pdf=source_pdf,
                staged_name=staged_name,
                content_list=content_list,
            )
        )

    return papers


def _select_source_pdf(paper_dir: Path) -> Path | None:
    candidates = sorted(paper_dir.glob("*_origin.pdf"))
    if candidates:
        return candidates[0]
    candidates = sorted(p for p in paper_dir.glob("*.pdf") if p.is_file())
    return candidates[0] if candidates else None


def _select_content_list(paper_dir: Path) -> Path | None:
    candidates = sorted(paper_dir.glob("*_content_list.json"))
    return candidates[0] if candidates else None


def _paper_title(paper_dir: Path, source_pdf: Path) -> str:
    title = paper_dir.name
    marker = ".pdf-"
    if marker in title:
        title = title.split(marker, 1)[0]
    title = title.removesuffix(".pdf").strip()
    return title or source_pdf.stem


def _normalize_title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def _staged_pdf_name(title: str, source_pdf: Path, input_dir: Path) -> str:
    safe_title = re.sub(r"[^A-Za-z0-9._ -]+", " ", title)
    safe_title = re.sub(r"\s+", " ", safe_title).strip(" ._-")
    safe_title = safe_title[:150].strip(" ._-") or source_pdf.stem
    rel = source_pdf.relative_to(input_dir).as_posix()
    digest = hashlib.sha1(rel.encode("utf-8")).hexdigest()[:8]
    return f"{safe_title}-{digest}.pdf"


# ============================================================
# MinerU raw cache materialization
# ============================================================

def stage_pdf(source_pdf: Path, staged_name: str, staging_dir: Path) -> Path:
    """把论文 PDF 放入 LightRAG 默认解析输入目录 inputs/。"""
    staging_dir.mkdir(parents=True, exist_ok=True)
    staged_pdf = staging_dir / staged_name
    if staged_pdf.exists():
        staged_size, staged_hash = compute_size_and_hash(staged_pdf)
        source_size, source_hash = compute_size_and_hash(source_pdf)
        if staged_size == source_size and staged_hash == source_hash:
            return staged_pdf
    shutil.copy2(source_pdf, staged_pdf)
    return staged_pdf


def prepare_mineru_raw_cache(
    paper: MinerUPaper,
    *,
    staged_pdf: Path,
    staging_dir: Path,
) -> Path:
    """将现有 MinerU 输出真实化为 LightRAG 的 *.mineru_raw 缓存。"""
    document_name = normalize_document_file_path(paper.staged_name)
    parsed_dir = parsed_artifact_dir_for(document_name, parent_hint=staging_dir)
    raw_dir = raw_dir_for_parsed_dir(parsed_dir)
    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(paper.content_list, raw_dir / "content_list.json")

    images_dir = paper.paper_dir / "images"
    if images_dir.is_dir():
        shutil.copytree(images_dir, raw_dir / "images")

    _copy_optional_mineru_artifacts(paper.paper_dir, raw_dir)
    _write_mineru_manifest(raw_dir, staged_pdf)
    return raw_dir


def _copy_optional_mineru_artifacts(paper_dir: Path, raw_dir: Path) -> None:
    optional_files: list[tuple[Path, str]] = []

    full_md = paper_dir / "full.md"
    if full_md.is_file():
        optional_files.append((full_md, "full.md"))

    layout_pdf = paper_dir / "layout.pdf"
    if layout_pdf.is_file():
        optional_files.append((layout_pdf, "layout.pdf"))

    for name in ("layout.json", "block_list.json"):
        artifact = paper_dir / name
        if artifact.is_file():
            optional_files.append((artifact, name))

    model_files = sorted(paper_dir.glob("*_model.json"))
    if model_files:
        optional_files.append((model_files[0], "middle.json"))

    content_v2_files = sorted(paper_dir.glob("*_content_list_v2.json"))
    if content_v2_files:
        optional_files.append((content_v2_files[0], content_v2_files[0].name))

    for source, target_name in optional_files:
        shutil.copy2(source, raw_dir / target_name)


def _write_mineru_manifest(raw_dir: Path, source_pdf: Path) -> None:
    source_size, source_hash = compute_size_and_hash(source_pdf)
    critical_size, critical_hash = compute_size_and_hash(raw_dir / "content_list.json")
    files = list(_iter_manifest_files(raw_dir))
    total_size = critical_size + sum(item.size for item in files)
    options = MinerUParserOptions.from_env()

    write_manifest(
        raw_dir,
        Manifest(
            source_content_hash=source_hash,
            source_size_bytes=source_size,
            source_filename_at_parse=source_pdf.name,
            critical_file=ManifestFile(
                path="content_list.json",
                size=critical_size,
                sha256=critical_hash,
            ),
            files=files,
            total_size_bytes=total_size,
            task_id="precomputed-mineru",
            api_mode=options.api_mode,
            options_signature=options.signature(),
            downloaded_at=datetime.now(timezone.utc).isoformat(),
        ),
    )


def _iter_manifest_files(raw_dir: Path) -> Iterable[ManifestFile]:
    for path in sorted(raw_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(raw_dir).as_posix()
        if rel in {"_manifest.json", "content_list.json"}:
            continue
        yield ManifestFile(path=rel, size=path.stat().st_size)


async def import_mineru_papers(
    papers: list[MinerUPaper],
    *,
    working_dir: Path,
    staging_dir: Path,
    process_options: str,
    limit: int | None = None,
    dry_run: bool = False,
) -> None:
    """多模态导入路径：复用 MinerU 产物并启动 LightRAG pipeline。"""
    if limit:
        papers = papers[:limit]

    print("=== mineru 多模态建库配置 ===")
    print(f"working_dir      : {working_dir}")
    print(f"staging_dir      : {staging_dir}")
    print(f"parse_engine     : {DEFAULT_PARSE_ENGINE}")
    print(f"process_options  : {process_options}")
    print(f"VLM enabled      : {VLM_PROCESS_ENABLE}")
    print(f"VLM model        : {VLM_MODEL}")
    print("")

    if dry_run:
        print(f"=== 将导入以下 {len(papers)} 篇 MinerU 论文 ===\n")
        for i, paper in enumerate(papers, 1):
            print(f"  [{i:3d}] {paper.staged_name}")
            print(f"        source : {paper.source_pdf}")
            print(f"        mineru : {paper.content_list}")
        print(f"\n=== 共 {len(papers)} 篇 ===")
        return

    staged_paths: list[str] = []
    cache_count = 0
    for paper in papers:
        staged_pdf = stage_pdf(paper.source_pdf, paper.staged_name, staging_dir)
        raw_dir = prepare_mineru_raw_cache(
            paper,
            staged_pdf=staged_pdf,
            staging_dir=staging_dir,
        )
        cache_count += 1
        staged_paths.append(staged_pdf.name)

    print(f"已 staging {len(staged_paths)} 个 PDF，复用 MinerU raw cache {cache_count} 个。")
    print("开始 LightRAG pipeline 入队与处理...\n")

    rag = await create_lightrag(working_dir=working_dir)
    track_id = await rag.apipeline_enqueue_documents(
        [""] * len(staged_paths),
        file_paths=staged_paths,
        docs_format=FULL_DOCS_FORMAT_PENDING_PARSE,
        parse_engine=DEFAULT_PARSE_ENGINE,
        process_options=process_options,
        track_id="fjsp-nfa-mineru-multimodal",
    )
    print(f"入队完成：track_id={track_id}")
    await rag.apipeline_process_enqueue_documents()
    print(f"\n全部完成：{len(staged_paths)} 篇论文已进入 LightRAG 多模态建库流程")


# ============================================================
# CLI 入口
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="构建 FJSP 知识库：只导入 MinerU 格式论文并启用多模态管线",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出待导入文件，不实际导入",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="最多导入 N 篇（用于测试）",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        metavar="DIR",
        help="论文资料根目录，默认指向 ALL-Input-Information/8-nfa-FJSP",
    )
    parser.add_argument(
        "--working-dir",
        type=Path,
        default=DEFAULT_WORKING_DIR,
        metavar="DIR",
        help="LightRAG 知识库存储目录",
    )
    parser.add_argument(
        "--staging-dir",
        type=Path,
        default=DEFAULT_STAGING_DIR,
        metavar="DIR",
        help="LightRAG pending_parse 源文件目录",
    )
    parser.add_argument(
        "--process-options",
        default=DEFAULT_PROCESS_OPTIONS,
        help="LightRAG 处理选项，默认 iteP：图片/表格/公式 + 段落语义分块",
    )
    args = parser.parse_args()

    os.environ.setdefault("LIGHTRAG_PARSER", "*:native-iteP,*:mineru-iteP,*:legacy-R")
    os.environ.setdefault("MINERU_API_MODE", "local")
    papers = collect_mineru_papers(args.input_dir)
    if not papers:
        print("未找到任何 MinerU 论文目录（需要 *_origin.pdf 和 *_content_list.json）。")
        return
    asyncio.run(
        import_mineru_papers(
            papers,
            working_dir=args.working_dir,
            staging_dir=args.staging_dir,
            process_options=args.process_options,
            limit=args.limit,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    main()
