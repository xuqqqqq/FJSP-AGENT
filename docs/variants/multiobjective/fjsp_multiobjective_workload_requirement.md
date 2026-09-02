# 工作负荷多目标 FJSP 问题说明

## 问题定义

本变体保留标准 FJSP 的全部硬约束：每个作业的工序按给定顺序执行，每道工序恰选一台候选机器，且同一机器上的加工区间不得重叠。它不增加新的硬约束，只改变评价目标。

## 目标

固定 evaluator 按以下词典序最小化：

1. `makespan`：所有工序的最晚结束时刻；
2. `max_machine_workload`：各机器上被选加工时长之和的最大值；
3. `total_workload`：全部被选加工时长之和。

令工序集合为 `O`，工序 `o` 选择机器 `m(o)`，对应加工时长为 `p[o,m(o)]`，则：

```text
makespan = max_o end[o]
machine_workload[m] = sum_{o:m(o)=m} p[o,m]
max_machine_workload = max_m machine_workload[m]
total_workload = sum_o p[o,m(o)]
```

这里的 workload 只统计被选加工时长，不统计等待、空闲或隐式 setup。该变体不允许 SDST、维修窗口等尾部与 `.mofjsp` 标记叠加。

## 比较规则

本项目当前采用严格词典序，而不是加权和或 Pareto 档案。任何候选只有在不恶化前序目标时，后序目标的改善才生效。论文中的 Pareto 前沿结果只能作为方法参考，不能直接冒充当前 evaluator 的同口径结果。

## 数据来源

输入主体使用公开的 Brandimarte 标准 FJSP 格式。本项目的正常规模验证实例由公开 `Mk01` 原始主体复制并增加 `.mofjsp` 文件标记，未修改任何作业、候选机器或加工时长。
