---
name: fjsp-agent-generated-solver
description: 在依据需求文档与 IO 文档生成、审查或演进独立自主编写的 FJSP/FJSP-SDST 求解器时使用，尤其适用于后端必须保持算法无关且求解器代码需由编码代理自主产出的场景。
---

# FJSP 自主生成求解器

## 触发条件

- 需要从需求文档和 IO 契约直接生成、修补或演进独立运行的 FJSP/FJSP-SDST 求解器。
- 后端必须保持算法无关，解码器、邻域和搜索逻辑必须由编码代理在候选代码中实现。
- 需要在不依赖平台内部求解器实现的前提下完成合法性、可演进性和独立 CLI 交付。

## 读取顺序

1. 先读当前需求文档、IO 契约、evaluator 协议和实例诊断。
2. 识别激活变体：alternative machines、sequence-dependent setup、time lag、no-wait、calendar、batching、transport、release/due date 或目标变化。
3. 读取 `references/solver_contract.md`。
4. 若项目中存在且当前变体已激活，读取 `knowledge/principles/agent_generated_variant_quality_contracts.md`。
5. 若是标准 FJSP 的 baseline 创建或合法性修补，且项目中存在，读取 `knowledge/references/standard_fjsp/standard_fjsp_agent_generated_reference_skeleton.md`。
6. 若要演进标准 FJSP 邻域，且 incumbent 已具备可达的 `assignment`、`machine_sequences` 与 progress decoder，读取 `knowledge/references/standard_fjsp/standard_fjsp_agent_generated_neighborhood_templates.md`。
7. 若是 FJSP-SDST 的自主求解器工作，且项目中存在，读取 `knowledge/references/sdst/awls_sdst_agent_generated_transfer_notes.md`。
8. 若当前变体需要 setup-aware sequencing，读取 `knowledge/references/sdst/agent_generated_decoder_neighborhood.md`。

## 执行步骤

1. 在写代码前先提出一个自然语言规则或 operator 假设。
2. 把 `solver foundation` 与优化方法族分开：从零任务必须先建立合法 parser、表示、decoder、
   self-check 和 warm-start incumbent，但这一步不构成 `constructive_search` 优于局部搜索、
   CP-SAT 或 memetic 的证据。合法 baseline 产生后，必须根据实例压力重新选择正式优化方法族。
3. 仅依据活动 IO 契约生成求解器代码，不导入后端求解器内部实现、evaluator 代码或既有解文件。
4. 实现独立 CLI，至少覆盖 `--input`、`--output`、`--seed` 与 `--time-limit-sec`。
5. 统一 operation identity、表示、构造、邻域、解码和输出路径，确保候选 move 失败时不会污染当前状态。
6. 提交 Core 前完成自检：活动 IO 解析、输出 schema、加工时长一致性、全工序覆盖、机器资格、precedence、non-overlap、变体约束、incumbent 保留和运行时边界。
7. 若 Core 已确认合法，再对照活动语义审查契约核实声明的方法是否真的在代码中闭合；若项目中存在 `knowledge/references/standard_fjsp/standard_fjsp_algorithm_semantic_review_contract.md`，标准 FJSP 需补读。
8. 若回路反馈含有 `agent_generated_quality_memory` 或 `algorithm_semantic_memory`，优先修复其反复出现的 parser、representation、constructor、decoder、variant-handling 或 self-check 缺口，再引入新的改进 operator。

## 权限与边界

- 本技能提供方法约束，不替代 solver 实现。
- 不要求后端编排提供 decoder 或 neighborhood 代码。
- 不把 FJSP-SDST 或其他变体算法硬编码进通用 pipeline、evaluator、parser、promotion 或 web 代码。
- 可复用知识模板，但必须保持 IO 派生、实例无关；不得复制已求解排程、固定工序顺序、基准特定分数或旧输出。
- promotion 仍只由固定的 Core evaluator 决定。
- 不把“当前没有 incumbent”当作正式优化必须选择 `constructive_search` 的理由；CP-SAT、
  local search 和 memetic 都可以消费统一 foundation 产生的 warm start，或在各自模块内初始化。

## 交付物

- 一个可独立运行的求解器入口。
- 与活动协议一致的解析、解码、搜索和输出路径。
- 对“保留了哪项既有机制、改动了哪个单一规则或 operator”的简明说明。
- 结构化自检证据，能定位到实际函数、变量或 guard。

## 验证与停止条件

- 只有在求解器能输出完整合法排程、满足 CLI 契约、保持统一表示并对失败候选保留 incumbent 时，才可进入 Core。
- 任一 move 或候选若超时、解码失败、违反资格/precedence/non-overlap/变体语义，必须丢弃并保留当前最佳可行解。
- 若发现重复的结构性缺口，先修复该缺口；未修复前停止扩大方法范围或宣称新搜索机制已就绪。
