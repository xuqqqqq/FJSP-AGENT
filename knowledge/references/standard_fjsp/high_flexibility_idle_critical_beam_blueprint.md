---
id: standard-fjsp-high-flexibility-idle-critical-beam
type: implementation_blueprint
title: 高柔性 FJSP 空闲间隙感知关键压力 Beam 构造蓝图
tags: [fjsp, high-flexibility, constructive-search, beam-search, idle-gap, critical-dispatch]
status: curated_reference
---

# 高柔性 FJSP 空闲间隙感知关键压力 Beam 构造蓝图

本卡用于候选机很多、浅层换机局部搜索覆盖率很低的标准 FJSP。核心方法是：对当前
ready operations 做关键压力排序，为每道工序选择可利用机器空闲间隙的候选机，再用
有界 Beam 保留多个部分排程。它属于构造式状态空间搜索，不是 AWLS、Tabu 或传统邻域
局部搜索。

## 1. 何时优先考虑

优先信号：

- `flexible_operation_ratio` 高且 `avg_candidate_count` 高，assignment 空间很大。
- 候选资格没有明显集中到少数机器，许多工序都有多个可信替代机器。
- 尚无可靠 incumbent 关键路径/关键块证据，或现有局部搜索只做一次、只检查几十个 move。
- 需要在短到中等预算内从零构造较强合法解，并希望同时探索工序选择顺序。
- 机器时间轴存在可利用空隙，简单 append-only 解码浪费明显。

这些信号只说明本方向值得验证，不证明它一定优于完整 VND、Tabu、群体方法或 CP。

## 2. 何时不要据此否定局部搜索

以下情况仍可优先局部搜索：

- incumbent 显示 makespan 被少数稳定关键机器块支配。
- 关键/近关键工序的有效替代机器很少，真正压力来自机器内顺序。
- 已实现可迭代的 VND/Tabu 闭环，包含换机后插入、完整解码、接受、记忆、扰动和独立
  incumbent，而不是一次浅扫描。
- 已有正式 evaluator 证据证明局部邻域在同类结构上持续 promotion。

因此正确结论是“高柔性会削弱浅层换机扫描的覆盖率”，不是“高柔性永远不适合局部
搜索”。

## 3. 状态与转移

Beam 状态至少包含：

```text
job_ready[j]                 每个作业下一道工序的最早可开始时刻
next_operation[j]            每个作业尚未调度的工序下标
machine_intervals[m]         每台机器已占用的有序 [start, finish) 区间
partial_schedule             已调度记录，仅用于输出和稳定 tie-break
```

每层只调度一道当前 ready operation：

1. 对每个作业的下一道工序，枚举其合法候选机。
2. 在候选机的有序区间中找 `start >= job_ready[j]` 的最早可容纳空隙。
3. 用空闲浪费、关键剩余工作量、完成时刻和稳定键选出该工序的机器方案。
4. 按关键压力给 ready operations 排序，只扩展前 `branch_width` 个。
5. 插入区间、推进作业状态，计算状态下界并进入下一层候选池。

机器 ID 若被直接用作数组下标，必须先验证为连续 `0..M-1`；否则建立显式
`machine_id -> dense_index` 映射。

## 4. 空闲间隙与关键压力

最早间隙插入必须扫描已有区间，不能只使用机器最后完成时刻：

```python
def earliest_gap(intervals, job_ready, duration):
    start = job_ready
    for busy_start, busy_finish in intervals:
        if start + duration <= busy_start:
            return start
        start = max(start, busy_finish)
    return start
```

一个可用的确定性优先级是：

```text
(idle_before_start,
 start - minimum_remaining_work,
 finish,
 -minimum_remaining_work,
 duration,
 stable_operation_key,
 machine)
```

它让搜索倾向于填充现有空隙，并优先推进剩余最短加工时间总和较大的作业。权重或元组
顺序必须由消融和正式评测校准，不能从单个实例固化为通用常量。

## 5. Beam 保留、下界与去重

每层扩展后按通用下界排序，例如取以下界的最大值：

- 当前所有作业和机器的已知完成时刻。
- `job_ready[j] + job j 未调度工序最短加工时间后缀和`。
- `(已调度工作量 + 剩余最短工作量) / machine_count` 的向上取整。

使用结构指纹去重：

```text
(tuple(job_ready), tuple(next_operation), tuple(tuple(machine_intervals[m])))
```

只按 makespan 或已调度工序数量去重会错误合并未来可达空间不同的状态。若使用更粗的
指纹，必须证明它不会删除有不同未来决策的状态。

## 6. 完整组合而非单一路径

Beam 应与少量互补构造入口共同竞争：最早完成、最短加工、最小机器就绪、最长剩余工作、
平衡关键压力，以及这些规则的 gap-aware 版本。最终只按合法 makespan 和稳定 tie-break
选择最优候选，并始终保留独立 incumbent。

```text
candidates = dispatch_rule_portfolio(problem, deadline)
candidates += bounded_idle_critical_beam(problem, deadline)
return best_legal(candidates, fallback=incumbent)
```

## 7. 必须补齐的工程边界

- 使用共享绝对 deadline；固定 `beam_width` 不是时间控制。
- 在剩余时间不足时停止新扩展并返回当前最佳完整合法解；部分排程不得序列化为答案。
- `beam_width` 与 `branch_width` 都应受实例规模和剩余预算约束。
- 每次插入后保持机器区间有序、无重叠，并验证作业 precedence。
- 固定 seed 下 tie-break 可复现；不能按实例名、BKS 或历史答案选择宽度和规则。
- 先记录规则组合、Beam 的独立贡献、状态数、去重率和耗时，再决定是否追加局部精修。

## 8. 模板能力边界

常见参考模板只对 ready operation 分支，而对一条工序的候选机仍做一次贪心选择。这可以
有效利用高柔性，但不等于完整探索 assignment 空间。若正式证据显示机器选择仍是瓶颈，
可在严格预算内对前 `k` 个机器方案共同分支，或把 Beam 结果交给完整的 assignment-aware
VND/Tabu；不能仅把 `beam_width` 调大后声称已解决机器分配搜索。

## 9. 验收证据

- 所有输出通过独立 evaluator 的完整合法性检查。
- gap-aware 入口确实产生过至少一次非尾部插入，而不是名义上支持空隙。
- Beam 实际保留过多个不同状态，且结构去重命中过重复状态。
- 与同预算的规则组合、浅层换机搜索分别比较，报告合法率、makespan 和运行时间。
- deadline 路径、空 Beam、单候选机、非连续机器 ID 或其显式拒绝路径都有测试。
