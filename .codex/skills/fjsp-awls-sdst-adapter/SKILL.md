---
name: fjsp-awls-sdst-adapter
description: 在把基于 AWLS 的柔性作业车间求解器适配或演进为带 sequence-dependent setup time 的 FJSP-SDST 时使用，尤其适用于选择完整 Method Package、编写 Worker Assignment，或在固定 IO/evaluator 契约下验证 setup-aware timing、邻域、tabu 与自适应评分的场景。
---

# FJSP AWLS-SDST 适配

## 触发条件

- 需要把 AWLS 类 FJSP 方法适配到带 sequence-dependent setup time 的 FJSP-SDST。
- 需要选择完整 Method Package、编写 Worker Assignment，或验证 setup-aware timing、邻域、tabu 和自适应评分。
- 任务受固定 IO/evaluator 契约约束，不能通过修改评测口径获得“适配成功”。

## 读取顺序

1. 先读当前任务文档与固定 evaluator 契约。
2. 读取 `knowledge/references/sdst/awls_sdst_adaptation_implementation.md`。
3. 若涉及 benchmark 或 promotion，读取 `knowledge/benchmarks/fjsp_benchmark_scope.md`。
4. 若涉及新的 variant/domain-pack/RAG 工作，读取 `knowledge/principles/fjsp_variant_domain_pack_rag.md`。
5. 若需要论文背景，再读取 `knowledge/references/sdst/awls_sdst_literature_notes.md`，但只把压缩后的结论带入 worker prompt。

## 执行步骤

1. 在代码前先提出一个自然语言规则或 operator 假设。
2. 将改动限定在当前 Worker Assignment 的目标范围内，不重写 parser、evaluator、solution schema 或 benchmark 语义。
3. 先完成 setup-aware 合法性闭合：确保 `update_time`、R/Q tails 和输出记录都尊重 setup gap。
4. 合法后再演进 N7/NK 类 move 评价和 `zi` 评分，使其反映 setup-aware 的 head/tail timing。
5. 先做一个小型活动任务 smoke，再决定是否扩大到更广的 benchmark。
6. 仅以 Core evaluator 结果决定 promotion，worker 自评只当诊断说明。

## 权限与边界

- parser 与 evaluator 行为必须冻结。
- 独立生成的 solver 必须自带 IO 派生 parser，不得导入 `harness_agent`；平台方法资产只可在验证时复用平台 parser。
- `instance.has_sequence_dependent_setup` 为 false 时，标准 FJSP 行为不得退化。
- LB/UB/BKS 只能用于诊断汇报，不能作为 solver 输入。
- 变体算法知识只放在 domain packs、knowledge cards、Skills 和 Method Packages 中，不写进通用后端编排。

## 交付物

- 一份围绕当前方向的 AWLS-SDST 适配方案或修补结果。
- 清晰说明当前阶段是在修 setup-aware 合法性、move 评价、扰动评分还是搜索控制。
- 与固定 evaluator 一致的验证记录，区分标准 FJSP 回归和 SDST 合法性 smoke。

## 验证与停止条件

- 必须至少包含一次标准 FJSP smoke 和一次来自活动任务的小型 SDST smoke。
- 只有在 setup-aware 合法性稳定后，才继续扩大质量主张。
- 若候选修改 parser/evaluator 语义、依赖硬编码实例名，或返回非法排程，立即停止并回滚该方向。
