# 空闲关键 Beam 实现模板

来源依据：用户提供的 `fjsp_idle_critical_solver.py` 快照。该模板只在 Main 显式选择
`beam_search` 时使用；高柔性实例的默认入口是
`high_flexibility_assignment_first_playbook.md`。原快照的 Beam 宽度是一个实现样例，
不是对所有实例有效的常量。

## 最早空闲间隙

```python
def earliest_gap(intervals, earliest, duration):
    start = earliest
    for busy_start, busy_finish in intervals:
        if start + duration <= busy_start:
            return start
        start = max(start, busy_finish)
    return start
```

区间必须保持按 start 排序。候选选择同时枚举 ready operation 和 eligible machine；高柔性时不能先固定一台最短机器再只对工序分支。

## 分层 Beam

```python
beam = [empty_partial_state(problem)]
for depth in range(problem.operation_count):
    expanded = []
    for state in beam:
        choices = []
        for op in ready_operations(state):
            for machine in machine_shortlist(op, state, deadline):
                duration = problem.duration[op, machine]
                start = earliest_gap(state.intervals[machine], state.job_ready[op[0]], duration)
                key = priority(op, machine, start, duration, state)
                choices.append((key, op, machine, start, duration))
        for choice in sorted(choices)[:state_branch_limit(state, deadline)]:
            child = extend_without_mutating_parent(state, choice)
            expanded.append((lower_bound(problem, child), fingerprint(child), child))

    beam = retain_distinct(expanded, width=width_for_remaining_budget(deadline))
    if not beam:
        break
```

下界至少取当前完成时刻、各 job ready 加剩余最短加工后缀、总最短工作量平均机器负载三者最大值。指纹至少包含 `job_ready`、`next_operation`、machine assignment/order 或等价 machine intervals；只按当前 makespan 去重会合并未来空间不同的状态。

## 入口组合与预算

保留少量机制互补入口，例如 earliest finish、shortest duration、least ready、longest remaining、load balance，以及 gap-aware 版本。记录每个入口的 makespan/耗时和 Beam 的 expanded/retained/pruned、profile collision、winner、机器 shortlist 分布。

宽度由 operation 数、平均候选机数、分支数、每层实测耗时和剩余 deadline 决定。若预算允许，可以把较大固定宽度作为候选实验；不能因为参考快照使用某个数值就硬编码到所有实例。部分状态永远不能覆盖完整 incumbent。
