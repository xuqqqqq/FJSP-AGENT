---
id: operator-critical-path-machine-block
type: operator
title: 关键路径机器块邻域
tags: [operator, critical-path, machine-block, local-search, makespan]
source: derived_from_fjsp_literature
status: seed
---

## 作用

构造式派工策略只能决定“当下选哪个动作”，容易在排程尾部留下长关键链。关键路径机器块邻域用于完整排程之后，找到决定 makespan 的关键路径，并尝试调整关键路径上同一机器的相邻工序顺序。

## 输入

1. 一个完整合法排程。
2. 每道工序的 job 前驱/后继关系。
3. 每台机器上的工序序列。

## 输出

一个或多个候选移动：

1. 关键机器块相邻交换。
2. 块首工序后移。
3. 块尾工序前移。

## 约束安全性

移动后不能简单改两个工序时间，必须重新解码或做全局时间传播。对标准 FJSP，可固定机器分配和机器序列后重新计算最早开始时间；对工业 FJSP，还要重新检查 setup、维修、转运和组批。

## 伪代码

```text
build precedence graph from job arcs and machine arcs
find one critical path ending at makespan operation
split critical path into maximal blocks on same machine
for each block:
    generate adjacent swap / head-tail insertion
    rebuild schedule by topological propagation
    keep candidate if feasible and makespan improves
```

## 适用阶段

第二阶段局部搜索或自演进候选算子。尤其适合机器顺序形成长关键块、构造解已难以继续改进的实例。

## 失败模式

1. 只交换非关键块，几乎无收益。
2. 交换破坏隐含前后关系，导致不可行。
3. 对 FJSP 只调机器序列、不调机器分配，可能改进有限。
