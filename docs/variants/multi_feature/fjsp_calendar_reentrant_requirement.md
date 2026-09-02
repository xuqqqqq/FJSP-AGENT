# 固定日历可重入 FJSP 需求

## 问题定义

该变体在标准 FJSP 的机器选择、工序前置和机器互斥之外，同时启用：

- 静态工件释放时间；
- 机器初始可用时间；
- 预先给定的机器不可用区间，工序不可抢占且不能跨越区间；
- 每个工件一个确定的连续回路，按固定次数完整展开。

目标仅为最小化 `makespan`。四项数据都属于硬约束，不增加能耗、优先级、维修决策或随机故障目标。

## 文献边界

方法和联合时序依据 Tamssaouet 等（2022），DOI `10.1016/j.ejor.2021.03.069`。该论文还包含 recipe-dependent SDST、recipe-dependent minimum lag 和按尺寸/recipe capacity 的并行组批，但它们与项目当前独立特性的定义不一致，因此本版本明确排除。

## 验收

固定 evaluator 必须同时执行 release、machine initial availability、downtime、完整回路展开、工件前置、候选机器、加工时长和机器互斥检查。缺少任一项都不能判为合法。
