---
id: agent-generated-variant-quality-contracts
type: principle
title: Agent 自主生成 FJSP 变体质量合同
tags: [fjsp, variants, agent_generated_solver, io-contract, self-check]
status: active
---

# Agent 自主生成 FJSP 变体质量合同

## 目的

当 Coding Worker 需要根据需求文档和 IO 文档编写或演化一个独立的 agent-generated FJSP solver 时，应使用这张卡。

后端不应提供 solver 算法本体。它应向 Worker 提供当前生效的特征合同，并要求用代码证据证明生成的 solver 已实现并验证相关约束。

## 生效特征接入

在选择方法前，先读取任务说明、IO 合同、evaluator 协议和实例诊断。与只罗列支持变体的大而泛的 RAG 卡片相比，解析后的诊断信息应被视为更强证据。

只有当某个变体特征在当前上下文中处于生效状态时，才实现它。
不要在标准 FJSP 实例中加入 SDST、no-wait、calendar、batching、transport、release-date、due-date 或 multi-objective 的假设。

## 证据合同

对每一个生成出来的 solver，都要引用具体源码符号来证明以下内容：

- 独立的 `--input`、`--output`、`--seed` CLI；
- 能处理全部 jobs、operations、candidate machines、durations 和当前变体数据的 parser；
- 在 parser、construction、decode、search 和 output 之间保持稳定的 operation identity；
- 完整覆盖、重复拒绝、机器资格、duration 一致性、precedence、non-overlap、runtime bounds 和 incumbent preservation。

`solver_contract_self_check` 中的叙述字段是证据字段，不是自由发挥的策略说明。`representation`、`decoder`、`variant_handling`、`runtime_bounds` 和 `incumbent_preservation` 都应分别点名提交 solver 中对应的源码符号。如果某个字段只写了意图，而代码里没有对应引用符号，那么在进入目标评估前，应先修复代码或自检。

对于已生效的变体，还要补充对应约束的证据：

- `sequence_dependent_setup`：同机相邻弧上的 setup 处理，以及在比较 sequence move 前完成 full decode；
- `no_wait`：每个后继都必须恰好在前驱完成时开始；
- `time_lag`：在前驱完成与后继开始之间应用 min/max lag bounds；
- `machine_calendar`：调度区间必须落在可用时间内，并避开 unavailable windows；
- `batching`：检查 batch capacity 和 compatibility；
- `transportation`：travel/transport time 必须计入后继就绪时间；
- `release_dates`：任何 operation/job 都不能早于解析出的 release time 开始；
- `due_dates`：如果它属于声明目标的一部分，就要计算 due-date、lateness 或 tardiness 项；
- `multi_objective`：候选比较必须遵循声明的 weights、priority order 或 Pareto rule。

## 修复优先级

如果评审指出 parser、representation、constructor、decoder 或变体证据缺失，应先修复这些结构性缺口，再增加新的 local-search 思路。一个连自身 active-feature 自检都无法通过的启发式改进，不应进入 Core evaluator 时间。

不要把历史上的精确 schedule 或目标 makespan 拷贝进 solver。
只保留可复用的方法经验，例如“operation-level 的 ready-list 构造在做 tie-break 打分前，应纳入当前生效变体的时间语义”。
