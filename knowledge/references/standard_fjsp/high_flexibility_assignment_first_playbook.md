---
id: standard-fjsp-high-flexibility-assignment-first
type: implementation_blueprint
title: 高柔性标准 FJSP assignment-first 实现卡
tags: [fjsp, high-flexibility, idle-gap, assignment-regret, assignment-trust-region, order-preserving-redecode]
status: curated_reference
---

# 高柔性标准 FJSP assignment-first 实现卡

本卡是 `high-flexibility-fjsp-playbook` 的 Worker 实现入口。高柔性标准 FJSP 默认按
earliest-gap、精确 operation pressure/regret、小半径 assignment trust-region 和保序重解码
推进。Beam、随机多起点和 telemetry-only 变体不是这条路线的替代实现。

## 1. 适用门槛

只在标准 FJSP、柔性工序比例和候选机器数较高、且候选加工时间跨度非零时使用。存在 SDST、
运输、机器日历或其它额外约束时，先切换匹配的变体 Skill。零跨度或低柔性工序继续按顺序
压力处理，不强行施加 assignment-first 偏好。

## 2. 构造阶段

先实现 earliest feasible gap。对 ready 工序 `op` 和候选机器 `m`，扫描机器有序区间得到
`gap_start(op, m)`，不能只读机器尾部完成时刻。

每道工序必须计算：

```text
duration_span(op) = max_m duration(op, m) - min_m duration(op, m)
pressure(op) = (candidate_count(op) - 1) * duration_span(op)
theoretical_fastest(op) = min_m duration(op, m)
assignment_regret(op, m) = assignment_cost(op, m) - theoretical_fastest(op)
```

默认 `assignment_cost(op, m) = duration(op, m)`。若任务书采用完成时间代价，必须把它作为单独
标量计算，不能从完整 score 元组做减法。

高 pressure 工序使用：

```text
(gap_start, assignment_regret, finish, -remaining_min_work, stable_op, machine)
```

低 pressure 工序保持顺序压力：

```text
(gap_start, -remaining_min_work, finish, stable_op, machine)
```

禁止把“最佳与次佳完整 score 元组差”、排名差、hash 差或编码后的元组差命名为
assignment regret。

## 3. Assignment trust-region

构造 activation 已通过但仍有差距时，只释放关键/近关键工序、其作业邻居和机器邻居：

1. 先枚举单工序换机。
2. 只有单步证据支持时才枚举严格两步改进链。
3. 每个候选都完整重解码并经过相同合法性检查。
4. 只接受 deterministic 的严格 makespan 改进；中间退化状态不能成为 incumbent。

换机后记录 incumbent 每台机器上的 operation rank。重解码时将未释放工序的 rank 作为稳定
tie-break，最大限度保留 region 外机器相对顺序。直接回到全局贪心重构不算
order-preserving redecode。

## 4. 候选分叉

候选必须对应不同机制：

- 构造阶段：exact pressure/regret 与低 pressure 顺序保护消融。
- 局部阶段：单工序 trust-region 与严格两步改进链。

只改变 seed、Beam 宽度、portfolio 顺序、日志字段或 score 权重，不算不同机制候选。相同阶段
连续合法但无提升时，推进到下一阶段或显式 pivot，不要继续复写同一规则。

## 5. Activation 合同

构造阶段至少输出并检查：

```text
diagnostics.activation.high_flexibility.pressure_evaluations > 0
diagnostics.activation.high_flexibility.positive_pressure_events > 0
diagnostics.activation.high_flexibility.regret_evaluations > 0
diagnostics.activation.high_flexibility.regret_tiebreak_applied > 0
diagnostics.activation.high_flexibility.gap_non_tail_insertions > 0
```

trust-region 阶段增加：

```text
diagnostics.activation.high_flexibility.trust_region_moves_evaluated > 0
diagnostics.activation.high_flexibility.order_rank_edges_preserved > 0
diagnostics.activation.high_flexibility.trust_region_moves_accepted > 0  # 声称改进时
```

`activation_checks` 必须使用这些机器可判定路径。自然语言描述、源代码中存在函数、或候选
makespan 变化都不能替代 activation。

## 6. 验收顺序

1. Core 确认全部工序、机器资格、precedence、non-overlap 和 makespan 合法。
2. Activation 证明本轮声明的机制实际执行。
3. 同预算、同 seeds 比较 source makespan。
4. 只有严格改进才晋级；合法但未激活的结果只能保留为 best legal 证据。
