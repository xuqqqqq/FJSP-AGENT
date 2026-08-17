---
id: paper-fjsp-scene-survey-2025-10-17
type: survey
title: FJSP 场景调研报告 2025-10-17
tags: [fjsp, survey, industrial-variants, constraints, aluminum-rolling, semiconductor, dynamic-fjsp, multi-objective, llm-heuristics]
source: knowledge/imported/local_papers/raw/FJSP场景调研报告10-17.pdf
status: local_report_indexed
---

# FJSP 场景调研报告 2025-10-17

## 来源

- 本地 PDF：`knowledge/imported/local_papers/raw/FJSP场景调研报告10-17.pdf`
- 页数：144
- 于 2026-06-25 从本地微信文件导入。

## 相关要点

这份报告是一份广泛的 FJSP 场景与变体综述，而不是单篇算法论文。它可作为一张本地总览图，帮助 harness 从标准基准 FJSP 走向工业变体。

报告覆盖：

- 工业场景：铝卷轧制和半导体制造；
- 经典 FJSP 建模：机器分配、工序排序、数学模型和析取图表示；
- 目标变体：makespan、常规/非常规指标、多目标优化、能耗、质量、延期和鲁棒性；
- 约束变体：time lag/no-wait、machine unavailability、batching、setup times、transportation、reentrance 以及替代/复杂工艺路线；
- 求解方法族：精确方法、派工/构造启发式、单解元启发式、群体元启发式、混合元启发式、RL/DRL、graph/attention 模型以及 LLM heuristic evolution。

## 对平台的影响

这份报告进一步说明，平台应把“standard FJSP”视为众多问题族能力之一，而不是最终范围。

对 `fjsp_harness_agent` 的含义：

- 问题族能力卡应扩展为带显式约束能力的变体卡，而不只是名称。候选变体标签包括 `time_lag_fjsp`、`machine_unavailability_fjsp`、`batching_fjsp`、`setup_time_fjsp`、`transportation_fjsp`、`reentrant_fjsp`、`alternative_route_fjsp`、`dynamic_fjsp` 和 `multi_objective_fjsp`。
- `Method Package` 应支持变体特有组件。例如：时滞感知解码、no-wait 块移动、维护窗口插入、批次形成、setup 感知序列评分、运输感知移动评分、重入环检查和路线选择邻域。
- `Context packet` 应携带当前生效的变体约束，并禁止 worker 在未得到用户确认新 IO contract 的情况下更改 parser/evaluator 语义。
- 知识选择应由标签驱动。凡任务涉及 FJSP 变体、工业约束、动态调度或 LLM/RL 辅助的启发式演化，都应检索这份调研。
- 评测必须继续受 contract 约束。对于变体，在可信任 worker 代码演化之前，平台需要新的 evaluator/validator。

## 有用的变体地图

| 变体/约束 | 重要性 | Harness 适配目标 |
| --- | --- | --- |
| Time lag / no-wait | 常见于半导体和高温工艺；会让朴素调度失效。 | 为 min/max lag 增加 parser/evaluator 字段；提供 decoder 和 no-wait block 组件。 |
| Machine unavailability | 维护与停机会把机器日历切碎。 | machine calendar validator；availability-aware insertion 组件。 |
| Batching | 常见于半导体和流程工业；会引入 batch formation 决策。 | batch schema；batch compatibility/capacity evaluator；batch neighborhood package。 |
| Setup times | sequence-dependent setup 会改变 move scoring 和 machine sequence 评估。 | setup matrix 输入合同；setup-aware objective 与 neighborhood 组件。 |
| Transportation | 跨机器或跨工厂物流会影响开始时间和目标值。 | transport-time schema；transport-aware decoder/evaluator。 |
| Reentrance | 半导体和轧制流程会重复访问机器；增加循环与资源竞争。 | reentrance-aware graph model 和 cycle check。 |
| Alternative routes | 作业可能有多条工艺路线，而不只是替代机器。 | state representation 中的 route-choice 层，以及 route-switch neighborhood。 |
| Dynamic/multi-objective FJSP | 真实生产会出现新作业、故障、能耗、质量和延期等目标。 | 滚动 contract 更新、multi-objective evaluator key，以及 policy/RL hook。 |

## 如何使用这张卡

在规划以下事项时使用这张卡：

- 新的问题族 capability card；
- 某个工业 FJSP 变体的新 Method Package；
- 面向标准公开基准之外 FJSP 约束的 RAG 查询；
- 未来面向 FJSP 专用 heuristic evolution 的 skill 设计。

不要把这张调研卡当作 solver 实现正确性的直接证据。它只用于设计与范围界定；正确性仍来自当前生效的任务合同和 evaluator。
