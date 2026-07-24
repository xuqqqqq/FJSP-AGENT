# FJSP 知识库

本目录保存可审计、可复用的领域资产。通用后端负责加载、筛选和记录证据，不在编排代码中预埋 decoder、邻域或搜索实现。

## 知识库与 Skill 的边界

判断标准不是“Agent 会不会读”，而是内容的性质和生命周期。

| 问题 | 放入知识库 | 放入 Skill |
| --- | --- | --- |
| 内容回答什么 | “这个问题/方法是什么、何时适用、为什么、有哪些证据和失败边界” | “Agent 接到这类任务后按什么顺序操作、读什么、产出什么、如何验收和停止” |
| 典型内容 | 问题定义、IO 事实、实例特征、方法适用性、算法原理、伪代码、实现参考、论文归纳、行为契约 | 触发条件、角色职责、读取顺序、工具与权限边界、执行步骤、交付物、activation/acceptance checks、停止条件 |
| 生命周期 | 跨任务稳定；变更时需要来源或技术理由 | 随工作流和 Agent 职责变化；应短小并可直接执行 |
| 是否包含具体运行结果 | 仅 `experiment_memory/` 可临时保存 | 不得写入 |
| 是否包含长代码示例 | 可以，放 `references/` 或 Method Package | `SKILL.md` 不放；只链接本 Skill 的 `references/` |

以下内容不得混放：

- 知识卡不得授予文件权限、指定 Agent 身份、规定模型或绕过 evaluator。
- Skill 不得把单次 makespan、seed、失败猜测或某个算例的临时结论写成通用规则。
- 同一段算法说明只保留一份；Skill 通过路径说明何时读取，不复制知识库正文。
- 参考代码是学习材料，不是默认答案。Coding Agent 可以采用、改写或拒绝，但必须满足当前 IO、合法性和实验合同。

## 分层

| 目录 | 内容 | 默认检索 |
| --- | --- | --- |
| `principles/` | 架构边界、质量契约、变体建模原则 | 是 |
| `benchmarks/` | IO 格式、数据集范围、LB/UB/BKS 事实 | 按问题族 |
| `capabilities/` | 当前 Agent 能力与缺口快照 | Main 显式审计时 |
| `references/` | 可复用算法说明、伪代码、代码模板和论文归纳 | 两阶段按标签选择 |
| `method_packages/` | 必须整体实现的方法包、行为契约和参考实现 | 仅在显式启用且精确匹配时激活 |
| `experiment_memory/` | 本周单次实验、种子、得分、失败路线和阶段性结论 | 否，只能显式回放 |
| `imported/` | 外部导入材料和本地资料索引 | 否 |

## 检索规则

1. 先按问题族读取 `principles/` 与 `benchmarks/` 中的契约事实。
2. 第一阶段只向 Main Agent 提供方法适用性与方法族比较卡，由 Main 选择一个方向并返回受控 `knowledge_query` 标签。
3. 第二阶段根据这些标签、实例变体和数量上限检索详细 `references/`，并只列出标签与变体都匹配的 Method Package。
4. Main Agent 再读取 `active_direction_knowledge` 和候选包合同，形成完整 `DirectionPlan` 与 `WorkerAssignment`；详细知识只有经过这次规划才进入 Worker 的 `read_set`。
5. Method Package 是可选的完整实现合同。它不在第一阶段露出，第二阶段也必须由 Main 显式选择，不能因为它是唯一候选而自动激活。
6. `capabilities/` 不作为算法模板，不能把当前缺口写成固定求解策略。
7. `experiment_memory/` 不参与默认 RAG。运行历史只能通过显式经验回放进入 Main；任何失败解释在进入稳定知识前都必须经人工审核和可复现实验证据确认。
8. `imported/` 保存来源，不能直接当成当前实现合同。

## 稳定知识要求

- 描述适用结构、状态表示、前置条件、伪代码、失败模式和验证方法。
- 不包含固定调度、previous output、目标算例答案或按实例名路由。
- 具体 LB/UB/BKS 只放 `benchmarks/`；具体运行 makespan、gap、seed 和输出路径只放 `experiment_memory/`。`capabilities/` 只写当前可复核能力，不保存实验流水账。
- 引用完整代码时必须保持实例无关，并由 Coding Worker 按当前需求与 IO 自行适配。

## 两种 Solver 模式

### Agent 自主生成独立求解器

Coding Worker 根据需求、IO、Main 的方向和二阶段检索结果自行生成独立 solver；若存在启用的方法包，再额外遵守该包的完整合同。生成代码不得 import `harness_agent`、evaluator 或历史解。

### 平台方法资产

`method_packages/` 中的参考实现用于教学、适配和行为对照。它可以被 Worker 阅读，但通用编排代码不得复制其算法实现。

两种模式共享 Core evaluator 和 promotion 规则，但 parser 所有权不同，不能在知识卡中混为一谈。

## 实验记忆保留规则

- 每周以北京时间周一 `00:00` 为边界；边界以前的实验记忆直接删除，不自动摘要、不迁入 Skill。
- 每份记忆必须声明 `recorded_at`、任务/算例、代码版本、模型、seed、预算、Core 结果、激活证据和证据路径；缺项时只视为临时观察。
- promotion 只能证明该候选在冻结评测下更优，不能单独证明某个机制具有通用因果效果。
- 要转为稳定知识，必须先剥离实例名、分数和路径，再由人工审核其复现、消融、适用范围与反例。
- `capabilities/` 只保留当前有效快照；陈旧快照按实验历史处理，不参与检索。

## 语言与维护

- 面向 Agent 和用户的标题、说明、工作流与结论统一使用中文。
- 算法名、论文题名、Skill 名、路径、代码标识、CLI/JSON 字段和公认缩写可保留英文。
- 引用英文原文时同时给出中文解释；不得让整份知识卡因保留术语而退回英文叙述。
- 新增资产前先判断生命周期：稳定方法进 `references/`，完整方法组合进 `method_packages/`，本周单次实验进 `experiment_memory/`，当前能力测量进 `capabilities/`，原始外部摘要进 `imported/`。

详细格式、模板和注册规则见 `knowledge/principles/knowledge_skill_authoring_standard.md`；现有资产分类清单与边界审计见 `knowledge/CATALOG.md`；历史迁移和门禁见 `docs/knowledge_skill_cleanup_plan.md`。
