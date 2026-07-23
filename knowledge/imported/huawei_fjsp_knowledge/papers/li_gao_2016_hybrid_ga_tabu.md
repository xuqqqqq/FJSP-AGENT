---
id: paper-li-gao-2016-hga-ts
type: paper
title: An effective hybrid genetic algorithm and tabu search for flexible job shop scheduling problem
tags: [fjsp, genetic-algorithm, tabu-search, hybrid, local-search]
source: https://gtk.uni-miskolc.hu/files/13518/10_An%2Beffective%2Bhybrid%2Bgenetic%2Balgorithm%2Band%2Btabu%2Bsearch%2Bfor%2Bflexible%2Bjob%2Bshop%2Bscheduling%2Bproblem.pdf
status: needs_fulltext
---

## 方法摘要

该方向将遗传算法用于全局搜索，把禁忌搜索用于局部强化。对 FJSP 而言，常见编码会同时处理两类决策：

1. 工序排序或工序选择顺序。
2. 每道工序的机器分配。

禁忌搜索通常围绕关键路径、机器序列或操作移动构造邻域，用于修复 GA 个体在局部结构上的不足。

## 适用问题

适用于标准 FJSP 的 makespan 最小化，也可扩展到带 setup、转运、维修窗口等工业变体，但扩展时必须重新设计解码器和可行性修复。

## 核心算法片段

1. 双层编码：机器分配向量 + 工序序列向量。
2. 初始种群混合随机生成和启发式生成。
3. 交叉/变异保持工序完整性。
4. 局部搜索重点处理关键路径上的瓶颈机器顺序。
5. 禁忌表避免近期移动反复撤销。

## 可迁移到本项目的点

1. 当前 Barnes smoke 与 best-known gap 平均 12.61%，应优先加入 GA/TS 或 ILS/TS 层，而不是继续只微调派工权重。
2. 华为工业算例也可以采用“双层结构”：第一层生成完整合法解，第二层对关键机器/关键工序链做局部重排。
3. 自演进 agent 可以先从知识库检索“编码、交叉、变异、禁忌邻域”片段，再生成候选算子。

## 风险与限制

1. 论文细节需补充全文阅读后确认，当前卡片只作为方法方向种子。
2. GA 参数较多，若没有强校验器和统一评估日志，容易过拟合小算例。
3. 对带复杂硬约束的工业 FJSP，普通交换/插入可能破坏可行性，需要采用重解码或修复型邻域。

## 后续动作

1. 人工补充或确认论文全文。
2. 提取具体编码、交叉、变异和 TS 邻域。
3. 先在 Barnes 上实现一个最小 HGA+TS demo，与当前 portfolio 派工策略比较。

