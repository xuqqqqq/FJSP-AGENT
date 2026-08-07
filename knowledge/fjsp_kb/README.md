# LightRAG 知识库文件说明

本目录是 FJSP 项目当前使用的 LightRAG 工作目录，保存已经导入论文的正文、分块、实体、关系、向量索引、图谱和 LLM 缓存。正常情况下这些文件由 LightRAG 自动读写，不建议手动编辑。若需要清库或删除半成品，应先停止正在运行的建库/检索进程，再整体备份或删除对应存储文件。

## 总体结构

LightRAG 会把一篇论文处理成几类数据：

1. 原始文档正文：完整 MinerU/PDF 解析结果。
2. 文本分块：按 token/chunk 切分后的段落。
3. 实体与关系：由 LLM 从 chunk 中抽取出的知识图谱信息。
4. 向量索引：实体、关系、chunk 的 embedding，用于相似度检索。
5. 查询/抽取缓存：避免重复调用 LLM。
6. 文档状态：记录每篇文档是否 processed、failed、duplicated 等。

## 文件说明

| 文件 | 作用 | 主要内容 | 是否建议手改 |
|---|---|---|---|
| `kv_store_doc_status.json` | 文档处理状态表 | 每个 `doc-*` 或 `dup-*` 的状态、文件名、摘要、chunk 数、错误信息、创建/更新时间 | 不建议 |
| `kv_store_full_docs.json` | 完整文档正文 | 每篇论文的完整解析文本，通常以 `{{LRdoc}}` 开头 | 不建议 |
| `kv_store_text_chunks.json` | 文本 chunk 存储 | 每个 chunk 的正文、token 数、来源文档 id、chunk id | 不建议 |
| `kv_store_full_entities.json` | 每篇文档的实体集合 | 按文档保存抽取出的实体名列表 | 不建议 |
| `kv_store_full_relations.json` | 每篇文档的关系集合 | 按文档保存实体关系对，如 `[源实体, 目标实体]` | 不建议 |
| `kv_store_entity_chunks.json` | 实体到 chunk 的倒排索引 | 某个实体出现在哪些 chunk 中，以及出现次数 | 不建议 |
| `kv_store_relation_chunks.json` | 关系到 chunk 的倒排索引 | 某条实体关系出现在哪些 chunk 中，以及出现次数 | 不建议 |
| `kv_store_llm_response_cache.json` | LLM 调用缓存 | entity/relation 抽取、关键词分析、多模态分析、查询生成等 LLM 响应缓存 | 可删除但不建议编辑 |
| `vdb_chunks.json` | chunk 向量库 | 文本 chunk 的 embedding、内容和元信息 | 不建议 |
| `vdb_entities.json` | 实体向量库 | 实体描述文本的 embedding、实体名、来源 chunk | 不建议 |
| `vdb_relationships.json` | 关系向量库 | 实体关系描述的 embedding、源实体、目标实体、来源 chunk | 不建议 |
| `graph_chunk_entity_relation.graphml` | 知识图谱文件 | 实体节点、关系边、chunk 关联形成的图结构 | 不建议 |

## 常见字段含义

### `doc-*`

LightRAG 为每篇进入知识库的文档生成的文档 id，例如：

```text
doc-d9df397d18c928b6ad43dd34f3f76716
```

同一篇文档的 chunk、实体、关系通常都会通过这个 doc id 关联。

### `dup-*`

重复导入记录，例如：

```text
dup-7e5dec3d41d954ae35b0394d626ea674
```

如果 `dup-*` 的状态是 `failed`，并且错误信息是 `File name already exists`，通常表示该论文此前已经成功导入，不代表原始论文处理失败。

### `status`

文档处理状态，常见值包括：

- `processed`：已经完整处理并写入知识库。
- `processing`：处理过程中，若长期停留可能是中断遗留状态。
- `failed`：处理失败，需查看 `error_msg`。

### `chunks_count`

该文档被切分出的 chunk 数量。chunk 数量越大，通常表示论文越长或 MinerU 解析内容更丰富。

### `content_summary`

文档摘要或正文开头片段，用于快速判断这条记录对应哪篇论文。

## 检索时如何使用这些文件

LightRAG 查询时通常会同时使用三类检索：

1. `vdb_chunks.json`：根据 query embedding 找相似文本段落。
2. `vdb_entities.json` 与 `kv_store_entity_chunks.json`：先找相关实体，再回到实体出现的 chunk。
3. `vdb_relationships.json` 与 `kv_store_relation_chunks.json`：先找相关关系，再回到关系出现的 chunk。

随后 LightRAG 会把实体、关系、chunk 合并成 final context，再交给 LLM 生成回答或虚拟知识卡。

## 维护建议

- 查看论文是否导入完成，优先看 `kv_store_doc_status.json`。
- 查看某篇论文的正文是否存在，看 `kv_store_full_docs.json`。
- 查看某篇论文切成了哪些段落，看 `kv_store_text_chunks.json`。
- 不要单独删除某一个 `vdb_*.json`，否则向量库与 KV 存储可能不一致。
- 若要完全重建知识库，建议停止服务后整体备份/清空整个 `knowledge/fjsp_kb` 目录，再重新导入。
- 若只是重复导入产生 `dup-* failed`，通常可以忽略。
