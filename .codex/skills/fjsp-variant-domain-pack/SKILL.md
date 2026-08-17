---
name: fjsp-variant-domain-pack
description: 在新增、适配或审查 FJSP 问题族变体时使用，例如 SDST、时间间隔/无等待、机器不可用、批处理、运输、可重入路线、动态到达或多目标 FJSP；用于指导领域包、RAG、技能、方法包与 evaluator 契约设计，同时保持求解器算法不进入通用后端编排。
---

# FJSP 变体领域包

## 触发条件

- 需要新增、适配或审查 FJSP 变体支持，例如 SDST、time-lag/no-wait、machine unavailability、batching、transportation、reentrant routes、dynamic arrivals 或 multi-objective FJSP。
- 需要设计或修订领域包、RAG、技能、方法包与 evaluator 契约，而不是直接把变体算法塞进通用后端。

## 读取顺序

1. 先读 `knowledge/principles/fjsp_variant_domain_pack_rag.md`。
2. 再读当前任务的 IO/evaluator 文档和实例诊断。
3. 若请求涉及 benchmark 口径，读取 `knowledge/benchmarks/fjsp_benchmark_scope.md`。
4. 若请求是 agent-generated FJSP-SDST 求解器演进而不是既有方法资产适配，读取 `knowledge/references/sdst/awls_sdst_agent_generated_transfer_notes.md`，再转用 `$fjsp-agent-generated-solver`。
5. 若引入工业或非标准约束，读取 `knowledge/references/general_fjsp/fjsp_scene_survey_2025_10_17.md`。

## 执行步骤

1. 在提代码前识别激活的变体约束：setup、lag/no-wait、calendar、batching、transport、route、release/due date 或目标变化。
2. 按领域包标签选择知识卡和一个已选方法包。
3. 保持 Worker Assignment 紧凑且只围绕当前方向。
4. 要求 worker 在写代码前先提出自然语言规则或 operator 假设。
5. 仅以 Core evaluator 结果决定 promotion。

## 权限与边界

- 变体算法知识放在领域包、知识卡、技能、方法包与 WorkerAssignment 中。
- 通用后端只负责加载契约、诊断、元数据、知识片段和 benchmark 报告。
- 对独立自主生成的 solver，只把 decoder 与 neighborhood 模式放在技能或知识引用中，不写进通用编排。
- 不把 SDST、no-wait、batching、transport 等变体启发式硬编码进通用编排。
- 未经用户确认新的 IO 契约前，不改变 parser/evaluator 语义。
- LB/UB/BKS 仅作诊断，不作 solver 输入。

## 交付物

- 与当前变体匹配的 Domain Pack 设计或修订方案。
- 最小必要集合：supported variants 与 alias、IO/evaluator 说明、目标与诊断、solver 或 adapter 入口、knowledge tags/cards、可选方法包、smoke 与 benchmark 梯度。

## 验证与停止条件

- 先证明合法性与 IO 稳定，再改进 makespan 或其他声明目标。
- 若变体支持依赖通用后端硬编码、改变固定 evaluator 口径，或未识别清楚活动约束，停止继续扩写。
