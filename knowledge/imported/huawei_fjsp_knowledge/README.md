# Huawei FJSP 导入资料

本目录保留早期知识库导入时的来源结构和元数据，用于追溯，不参与默认 RAG。

## 内容

- `papers/`：论文摘要和早期项目解读。
- `codebases/`：外部实现与代码库说明。
- `datasets/`：外部数据集和历史评测口径。
- `schema.md`：早期卡片格式。
- `todo_downloads.md`：尚未补齐的外部资料。

早期 `operators/` 中经过清理且仍可复用的实现知识已迁移到 `knowledge/references/standard_fjsp/`；带具体算例、种子和结果的 `lessons/` 已迁移到 `knowledge/experiment_memory/imported_runs/`。迁移后的文件保留原有 frontmatter/source 字段以维持来源关系。

## 使用边界

1. 需要核对来源时显式读取本目录。
2. 不把这里的历史得分、实例名分支或旧平台接口直接传给 Coding Worker。
3. 可复用结论必须先去除单次实验事实，再进入 `references/` 或 Method Package。
4. 算法实现仍由 Coding Worker 根据当前需求、IO 和选中方法包生成；通用后端不得复制这里的算法内容。
