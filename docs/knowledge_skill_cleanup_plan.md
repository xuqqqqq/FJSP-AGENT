# 知识库与 Skill 整理计划

## 目标

把当前混合存放的稳定方法知识、执行流程、能力快照和单次实验记录拆开，形成可检索、可追溯、可验证的 FJSP 知识体系。整理只改变知识资产的位置和检索边界，不向通用后端加入任何求解算法实现。

## 内容生命周期

| 层级 | 内容 | 默认进入任务上下文 | 维护规则 |
| --- | --- | --- | --- |
| `principles/` | 架构边界、质量契约、变体建模原则 | 是 | 只保留跨算法、跨算例的稳定规则 |
| `benchmarks/` | IO 格式、数据集范围、LB/BKS 事实 | 按问题族 | 只记录可核验事实，不写搜索结论 |
| `capabilities/` | Agent 当前能力和缺口快照 | Main 可读，Worker 默认不读 | 带日期和证据，不冒充方法知识 |
| `references/` | 可复用算法说明、伪代码、实现模板、论文归纳 | 由 Domain Pack/Method Package 精确选择 | 不包含目标算例得分和固定调度 |
| `method_packages/` | 一次必须完整交付的方法组合及行为契约 | 每轮只激活一个 | 契约、参考实现和适用边界必须一致 |
| `experiment_memory/` | 单次运行、种子、得分、失败尝试和阶段性经验 | 否 | 仅通过显式经验回放进入 Main，不参与默认 RAG |
| `imported/` | 外部导入材料及来源说明 | 否 | 保留来源，不作为默认实现入口 |

## 目标目录

```text
.codex/skills/
  fjsp-solver-optimizer/
  fjsp-agent-generated-solver/
  fjsp-awls-sdst-adapter/
  fjsp-variant-domain-pack/

knowledge/
  principles/
  benchmarks/
  capabilities/
  references/
    general_fjsp/
    standard_fjsp/
    sdst/
  method_packages/
  experiment_memory/
    agent_generated/
    awls_sdst/
    imported_runs/
  imported/
    huawei_fjsp_knowledge/
```

## 迁移原则

1. 先补路径解析、契约继承、实验记忆隔离和后端边界测试，再移动文件。
2. `fjsp-solver-optimizer` 作为通用优化入口，沿用“短 SKILL + 按需 references”的格式。
3. 专项 Skill 只负责触发条件、执行顺序和边界；完整算法材料放入各自 `references/` 或项目知识库。
4. 标准 FJSP skeleton、邻域模板和语义审查契约移入 `references/standard_fjsp/`，不再藏在 imported 目录。
5. AWLS-SDST 的稳定方法说明移入 `references/sdst/`；带具体算例、日期、种子或得分的材料移入 `experiment_memory/`。
6. 能力快照移入 `capabilities/`，不得作为 Worker 的默认方法卡。
7. 外部代码库、数据集和论文摘录保留在 `imported/` 并保留来源说明。
8. 所有 JSON、Skill、测试和文档引用一次性更新；不保留会掩盖漏改的旧路径兼容别名。

## 验证门禁

- 所有 Domain Pack、知识卡、Skill reference、Method Package implementation/contract 路径存在。
- Method Package `extends` 链可加载，父子组件和 coupled group 合并结果不变。
- `experiment_memory/` 不进入默认知识检索或 Worker assignment。
- 通用后端不包含 FJSP decoder、邻域或搜索实现。
- 可复用知识不包含会变化的 benchmark score、固定 schedule 或 previous output。
- Skill frontmatter 和目录结构通过 `quick_validate.py`。
- 全量单元测试通过，再做一次不调用模型的有界检索/上下文构建验证。

## 执行顺序

1. 完成当前 repair assignment 回归。
2. 增加迁移前门禁测试。
3. 导入并校正通用 `fjsp-solver-optimizer` Skill。
4. 移动知识资产并更新 Domain Pack/Method Package/Skill 路径。
5. 删除生成缓存和失去用途的重复入口。
6. 运行 Skill 校验、定向测试、全量测试和有界上下文验证。
