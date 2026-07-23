---
id: operator-hgtsa-fjsp-n8-k-insertion-blueprint
type: operator
title: HGTSA-FJSP 简化复现蓝图
tags: [operator, fjsp, hgtsa, n8, k-insertion, tabu-search, critical-path]
source: knowledge/imported/huawei_fjsp_knowledge/papers/xiejin_2022_hgtsa_jobshop.md
status: seed
---

## 作用

该蓝图用于把谢晋博士论文中的 FJSP HGTSA 思想转成可适配当前 IO 的最小版本。目标不是一次完整复现论文，而是建立可验证的强局部搜索层。

## 输入

1. 标准 FJSP 实例。
2. 一个完整合法排程。
3. 每道工序的候选机器。
4. 当前机器序列和机器分配。

## 输出

改进后的完整合法排程，以及 move 日志：

```text
move_type: N8 | k-insertion
operation: (job_id, op_id)
from_machine:
to_machine:
insert_position:
old_makespan:
new_makespan:
tabu_key:
```

## 最小实现步骤

### 1. 机器序列表示

从排程中提取：

```text
machine_sequences[machine_id] = [(job_id, op_id), ...]
machine_assignment[(job_id, op_id)] = machine_id
```

### 2. 主动解码

给定机器序列和工件前后关系，按拓扑约束计算最早开始时间。如果机器序列与工件约束产生环，则候选解不可行。

### 3. 关键路径提取

建立包含两类弧的析取图：

1. 工件内前后工序弧。
2. 同机器序列相邻工序弧。

从虚拟源点到虚拟汇点求最长路径，得到关键路径和关键块。

### 4. N8 简化邻域

先实现安全子集：

1. 对关键块内相邻工序做交换。
2. 将关键块首工序向后插入少量位置。
3. 将关键块尾工序向前插入少量位置。

每个 move 后主动解码，若无环且 makespan 改善则可接受。

### 5. k-insertion 简化邻域

对关键路径上的工序：

1. 枚举其它候选机器。
2. 在目标机器上只枚举少量位置，例如关键时间窗附近、机器尾部、最早可插入位置。
3. 主动解码，合法后评价 makespan。

### 6. Tabu Search

建议初始参数：

```text
tabu_length = 15 + job_count / machine_count
no_improve_limit = 100 到 500
neighbor_limit_per_iter = 100 到 500
```

短预算版本先用于 smoke，确认 gap 是否下降；长预算版本再用于论文/报告实验。

## 约束安全性

标准 FJSP 中，安全性由“机器序列 + 工件前后约束 + 主动解码 + 环检测”保证。工业华为算例中，还必须叠加现有校验器。

## 适用阶段

1. 在结构相近的标准 FJSP 算例上稳定缩小相对 best-known gap。
2. LLM 自演进候选算子模板。
3. 华为工业算例第二阶段局部修复的思想来源。

## 失败模式

1. 只实现 N8，不做机器重分配，可能改进有限。
2. k-insertion 枚举太多会爆炸，必须裁剪。
3. 如果主动解码不正确，会出现机器序列无环判断错误。
4. 如果只接受改善 move，可能陷入局部最优；TS 需要允许非改善 move。
