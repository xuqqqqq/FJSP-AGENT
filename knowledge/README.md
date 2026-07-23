# FJSP 知识库

本目录只保存可审计的领域资产。通用后端负责加载、筛选和记录证据，不在这里之外预埋 decoder、邻域或搜索实现。

## 分层

| 目录 | 内容 | 默认检索 |
| --- | --- | --- |
| `principles/` | 架构边界、质量契约、变体建模原则 | 是 |
| `benchmarks/` | IO 格式、数据集范围、LB/UB/BKS 事实 | 按问题族 |
| `capabilities/` | 带日期的 Agent 能力与缺口快照 | Main 显式审计时 |
| `references/` | 可复用算法说明、伪代码、代码模板和论文归纳 | 两阶段按标签选择 |
| `method_packages/` | 必须整体实现的方法包、行为契约和参考实现 | 仅在显式启用且精确匹配时激活 |
| `experiment_memory/` | 单次实验、种子、得分、失败路线和阶段性结论 | 否，只能显式回放 |
| `imported/` | 外部导入材料和本地资料索引 | 否 |

## 检索规则

1. 先按问题族读取 `principles/` 与 `benchmarks/` 中的契约事实。
2. 第一阶段只向 Main Agent 提供方法适用性与方法族比较卡，由 Main 选择一个方向并返回受控 `knowledge_query` 标签。
3. 第二阶段根据这些标签、实例变体和数量上限检索详细 `references/`，并只列出标签与变体都匹配的 Method Package。
4. Main Agent 再读取 `active_direction_knowledge` 和候选包合同，形成完整 `DirectionPlan` 与 `WorkerAssignment`；详细知识只有经过这次规划才进入 Worker 的 `read_set`。
5. Method Package 是可选的完整实现合同。它不在第一阶段露出，第二阶段也必须由 Main 显式选择，不能因为它是唯一候选而自动激活。
6. `capabilities/` 不作为算法模板，不能把当前缺口写成固定求解策略。
7. `experiment_memory/` 不参与默认 RAG。运行历史只能通过显式经验回放进入 Main，并且只有 promotion 加权威语义审查支持的机制可成为保护事实。
8. `imported/` 保存来源，不能直接当成当前实现合同。

## 稳定知识要求

- 描述适用结构、状态表示、前置条件、伪代码、失败模式和验证方法。
- 不包含固定调度、previous output、目标算例答案或按实例名路由。
- 具体 LB/UB/BKS 只放 `benchmarks/`；具体运行 makespan、gap、seed 和输出路径只放 `capabilities/` 或 `experiment_memory/`。
- 引用完整代码时必须保持实例无关，并由 Coding Worker 按当前需求与 IO 自行适配。

## 两种 Solver 模式

### Standalone Agent-Generated

Coding Worker 根据需求、IO、Main 的方向和二阶段检索结果自行生成独立 solver；若存在启用的方法包，再额外遵守该包的完整合同。生成代码不得 import `harness_agent`、evaluator 或历史解。

### Platform Method Asset

`method_packages/` 中的参考实现用于教学、适配和行为对照。它可以被 Worker 阅读，但通用编排代码不得复制其算法实现。

两种模式共享 Core evaluator 和 promotion 规则，但 parser 所有权不同，不能在知识卡中混为一谈。

## 维护

新增资产前先判断生命周期：稳定方法进 `references/`，完整方法组合进 `method_packages/`，一次实验进 `experiment_memory/`，能力测量进 `capabilities/`，原始外部摘要进 `imported/`。对应迁移和门禁见 `docs/knowledge_skill_cleanup_plan.md`。
