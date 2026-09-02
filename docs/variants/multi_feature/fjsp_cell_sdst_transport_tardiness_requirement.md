# FJCS-SDFSTs-ITTs 需求说明

## 文献与数据

- 方法论文：Deliktaş et al., *Evolutionary algorithms for multi-objective flexible job shop cell scheduling*, DOI `10.1016/j.asoc.2021.107890`。
- 数据论文：Deliktaş et al., *A benchmark dataset for multi-objective flexible job shop cell scheduling*, DOI `10.1016/j.dib.2023.109946`。
- 固定公开数据：Mendeley Data DOI `10.17632/rtzby7pv7m.1`，共 43 个实例。

## 冻结语义

每个工件属于一个 part family，具有固定且可回流的完整工序路线和 due date。每道工序可选一个或多个物理机器副本；每个物理机器属于一个 cell。

- 同一物理机器上的相邻工序按前后工件 family 查有向 setup matrix。
- 同一工件相邻工序若跨 cell，后继开始时间不得早于前驱结束时间加有向运输时间；同 cell 为 0。
- 回流已在实例的完整路线中显式展开，不允许按唯一逻辑机器去重。
- 所有物理机器副本分别互斥。
- 固定目标为严格词典序 `(makespan, total_tardiness)`，其中 `total_tardiness = sum(max(C_j-d_j, 0))`。

论文原任务使用 Pareto 多目标。平台词典序是可复现测试口径，不能把平台单点结果冒充论文 Pareto 前沿。
