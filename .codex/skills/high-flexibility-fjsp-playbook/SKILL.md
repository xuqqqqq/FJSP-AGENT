---
name: high-flexibility-fjsp-playbook
description: 在标准 FJSP 的 `makespan` 优化中，当高柔性实例主导瓶颈时，指导编码代理组合 `earliest-gap` 构造、按工序 `pressure/regret` 的分配优先规则，以及保序重解码的小半径分配信任域；不适用于仅低柔性零跨度实例或含额外非标准约束的问题。
---

# 高柔性标准 FJSP 的分配优先收敛打法

## 何时触发

在下面条件同时成立时触发本技能：

- 目标是最小化标准 FJSP 的 `makespan`。
- 约束仍是标准工序前驱与机器互斥，没有额外的换型、运输、释放时间或多资源约束。
- 实例中有一部分工序具备较高机器选择度，并且不同机器上的加工时间存在非平凡跨度。
- 当前求解器已经能稳定构造可行解，但高柔性实例仍主导剩余分数。

不要在下面情况触发：

- 全部实例都接近低柔性且加工时间跨度为零。
- 目标不是 `makespan`，或者需要 CP-SAT / MIP / 大规模随机多起点等另一类求解范式。
- 用户明确优先别的目标，例如纯运行时间优化或非标准约束适配。

## 先做结构判别，不要先堆搜索

先统计每个实例或每类工序的结构量：

- 候选机器数分布。
- 多机器工序占比。
- 每道工序的 `duration span = max(duration) - min(duration)`。
- 当前解里关键路径附近还能换机的工序数量。

把高柔性压力理解成“分配空间是否足够大且足够有差异”。一个简单且实用的局部量是：

`pressure = (候选机器数 - 1) * duration_span`

它不需要是跨项目常量阈值，但应被当作“这道工序更像 assignment 问题还是 sequence 问题”的信号。

## 第一阶段：先把解码器换成 earliest-gap，而不是继续串行 machine-ready

如果当前构造仍是只看 `machine_ready` 的串行排程，先改成 earliest-gap / earliest feasible insertion 解码：

- 对每个 ready 工序与候选机器，找该机器时间线上的最早可插入位置。
- 用确定性键选下一步，优先保持 `start-first`，再在 tie-break 中表达结构信息。
- 先吃掉机器空隙利用率带来的大头收益，再讨论更细的 assignment 或 sequence 邻域。

原因：在高柔性实例上，很多后续策略都依赖“候选机器真实最早可开工时间”，而不是串行近似的 `machine_ready`。

## 第二阶段：对高柔性工序使用按工序的分配优先规则

不要只在整实例级别切换一套规则。对每个 ready 工序，按工序自身的 `pressure` 选择不同的 tie-break 侧重点：

- `pressure` 很低时：保持顺序主导，优先更早开始，并偏向更长剩余链。
- `pressure` 为正且不小的时候：在 `start-first` 之后，优先更小的 assignment regret。

这里的 assignment regret 必须是同一道工序上的标量分配代价差：

```text
theoretical_fastest(op) = min(duration(op, machine))
assignment_regret(op, machine) = assignment_cost(op, machine) - theoretical_fastest(op)
```

默认令 `assignment_cost = duration(op, machine)`；若任务书明确采用完成时间代价，也必须先
单独计算该标量，再减去 `theoretical_fastest`。高 pressure 的确定性选择键保持
`(earliest_gap_start, assignment_regret, finish, -remaining_work, stable_key)`；低 pressure
则保持顺序压力，例如 `(earliest_gap_start, -remaining_work, finish, stable_key)`。

不要把最佳与次佳候选的完整 score 元组之差、元组的某个编码值、或两个候选排名之差
命名为 assignment regret。那类差值混入了 start、finish、load 和稳定键，不能证明
分配优先机制已实现。

这一步的重点不是“盲目选最快机器”，而是在 earliest-gap 语义下，优先选择相对该工序自身最不吃亏的分配，同时保留剩余链信息，避免把低柔性工序也拖进过强的 assignment 偏好。

如果一个 benchmark 混合了低柔性和高柔性实例，目标通常不是用一条统一规则吃遍所有结构，而是：

- 让低柔性、零跨度部分保持稳定，不被高柔性启发式拖坏。
- 把 assignment-aware 的细化收益集中施加在真正高压的工序上。

## 第三阶段：只对高柔性瓶颈开启小半径 assignment trust-region

当第二阶段后，高柔性实例仍明显落后，再加一个很小的 assignment trust-region，而不是立刻上更深的 sequence 搜索。

推荐做法：

- 只从关键/近关键工序池、以及它们的作业邻居和机器邻居中取候选。
- 先尝试单工序换机；有证据时再尝试 2 步严格改进链。
- 每一步都要求属于最终严格改进链的一部分，不保留中间退化状态。
- 只接受确定性、可复现的严格改进。

关键点不在“换多少次机”，而在“如何重解码”。

## 局部换机后，重解码要保留 incumbent 的顺序秩

这是本技能里最不该丢掉的细节。

对高柔性实例做单工序或双工序换机后，如果直接把解重新交给全局贪心构造，很多本来有效的局部 move 会被新的贪心分叉冲掉，收益消失，甚至变差。

更稳的做法是：

- 保留 incumbent 的 machine order rank 或等价的局部顺序信息。
- 在重解码时，把这些顺序秩放进 tie-break，做“保序修复式重解码”。
- 让变化尽量局限在你显式打开的 trust-region 内，而不是让整个解被重建成另一套结构。

如果一个 promising move 只有在这种保序重解码下才能兑现收益，说明真正有效的机制是“局部 assignment repair”，不是“再跑一遍全局贪心”。

## 对高柔性实例，优先试什么，优先停什么

优先尝试：

1. `earliest-gap` 解码。
2. 按工序 `pressure + assignment regret` 的分配优先构造。
3. 小半径、关键池约束的 assignment trust-region。
4. 在提交前，按实例分别比较是否真的是高柔性实例在进步。

优先停止：

- 更深的 2 到 3 步 assignment 链如果已经不能继续严格压低高柔性实例。
- 固定 assignment 的小半径关键块顺序微调，如果高柔性实例上没有明确边际收益。
- 仅靠 wait/load/slack 特征改造构造键，却没有超过现有 `start-first + regret-first` 的强构造。

这类路线不是永远无效，但在已经具备强构造和小 trust-region 的前提下，往往收益递减很快。没有新结构信号时，不要靠继续加深局部搜索半径硬拖。

## 如果用户明确优先高柔性实例，提交标准也要跟着改

当总分由多个实例组成，而用户明确要求“优先改善高柔性实例”时，不要只看总分更低：

- 必须先看目标实例是否真的改善。
- 只改善低柔性实例的路线，即便总分更低，也不应优先提交。
- 对外报告时要把目标实例与非目标实例的变化拆开写。

这条规则能避免被“总分更好但方向错了”的路线带偏。

## 实施时的边界纪律

- 不要改 evaluator、trusted 数据、测试边界或 benchmark 协议来帮局部路线落地。
- 若一条局部路线只有在同时改测试、改评测边界或放松可信约束时才显得更好，把它视为无效候选。
- 诊断信息可以放在 solver 内部，但不要改变正式输出协议。
- 所有改动都应保持 deterministic，并在同一冻结口径下验证。

## 激活证据必须可由机器判定

任务书只要求当前阶段实际实现的检查，不要用自然语言描述代替 JSON 路径。构造阶段至少记录：

- `diagnostics.activation.high_flexibility.pressure_evaluations > 0`
- `diagnostics.activation.high_flexibility.positive_pressure_events > 0`
- `diagnostics.activation.high_flexibility.regret_evaluations > 0`
- `diagnostics.activation.high_flexibility.regret_tiebreak_applied > 0`
- `diagnostics.activation.high_flexibility.gap_non_tail_insertions > 0`

trust-region 阶段再要求：

- `diagnostics.activation.high_flexibility.trust_region_moves_evaluated > 0`
- `diagnostics.activation.high_flexibility.order_rank_edges_preserved > 0`
- 若声称该阶段带来改进，`trust_region_moves_accepted > 0`

候选必须按机制分叉，而不是只改参数：构造阶段可比较 exact-regret 与低 pressure 顺序保护；
已有强构造 incumbent 后，应比较单工序 trust-region 与严格两步改进链。Telemetry-only、随机
seed、Beam 宽度或 portfolio 顺序变化不算新的高柔性方法族候选。

## 最小实验顺序

遇到高柔性标准 FJSP 时，优先按下面顺序推进：

1. 统计柔性与 duration span，确认高柔性瓶颈是否真实存在。
2. 把构造换成 earliest-gap。
3. 用按工序的 `pressure + assignment regret` 细化构造优先级。
4. 若高柔性实例仍落后，再加小半径 assignment trust-region。
5. 若这一步已经变平，不要急着扩更深 sequence 微调或更多 wait/load 特征；先把“无收益”当成有效证据。

这个顺序的目标不是穷举所有启发式，而是尽快找到：高柔性实例的剩余空间究竟还在 assignment，还是已经进入明显收益递减区。
