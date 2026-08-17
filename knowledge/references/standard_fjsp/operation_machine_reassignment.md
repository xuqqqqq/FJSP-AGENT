---
id: operator-operation-machine-reassignment
type: operator
title: 候选机器重分配邻域
tags: [operator, machine-assignment, fjsp, local-search]
source: derived_from_fjsp_literature
status: seed
---

## 作用

FJSP 相比 JSP 的核心灵活性是工序可选机器。若只固定初始机器分配再做机器序列搜索，可能错过大幅改善。该算子尝试把关键工序重新分配到其它候选机器。

## 输入

1. 完整排程。
2. 关键路径或尾部高延迟工序集合。
3. 每道工序候选机器及加工时间。

## 输出

候选移动：

```text
move(operation=(job_id, op_id), from_machine=A, to_machine=B, insert_position=p)
```

## 约束安全性

移动后需要在目标机器选择插入位置，并重新计算完整排程。对标准 FJSP 可通过机器序列 + job precedence 重解码保证可行。

## 适用阶段

1. 构造式排程后的局部改进。
2. Tabu Search 邻域。
3. GA 个体变异。

## 失败模式

1. 只按加工时长最短重分配，会把负载推到瓶颈机器。
2. 只按机器空闲重分配，可能拉长工件后续链。
3. 候选机器很多时，邻域膨胀，需要先筛选关键工序。

