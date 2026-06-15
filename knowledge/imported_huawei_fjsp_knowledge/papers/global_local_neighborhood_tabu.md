---
id: paper-global-local-neighborhood-tabu-fjsp
type: paper
title: Global-local neighborhood search algorithm and tabu search for FJSP
tags: [fjsp, tabu-search, global-local, neighborhood, critical-path]
source: https://pmc.ncbi.nlm.nih.gov/articles/PMC8176541/
status: seed
---

## 方法摘要

该方向强调在 FJSP 中结合全局搜索和局部邻域搜索，并使用 Tabu Search 强化局部改进。对当前项目最有价值的是“邻域设计”思想：不能只根据单步派工评分做构造式调度，还要在完整排程上识别关键结构并重排。

## 适用问题

适合标准 FJSP 的 makespan 优化。若扩展到顺序相关 setup、维修窗口或组批机台，需要让邻域移动经过可行性过滤或重解码。

## 核心算法片段

1. 先生成完整可行解。
2. 根据关键路径、瓶颈机器或局部块构造候选移动。
3. 对候选移动计算 makespan 改善。
4. 使用 tabu 表避免循环。
5. 允许满足 aspiration 条件的 tabu 移动破禁。

## 可迁移到本项目的点

1. Barnes gap 最大的 mt10x/mt10xxx 接近 18%，应优先查看关键路径和机器块，而不是继续调派工权重。
2. 可把“关键机器块内相邻交换”和“插入到同机器不同位置”做成受控算子，供 LLM 选择启用。
3. 华为算例中也可用类似思想定位临界工件尾部链，但必须叠加 setup、维修、组批约束检查。

## 风险与限制

1. 需要完整且高效的排程重算函数。
2. 普通机器序列邻域对 FJSP 的机器重分配能力有限，需要配合机器选择邻域。
3. 对工业扩展约束，直接移动可能引发大量不可行候选。

## 后续动作

1. 在标准 FJSP runner 中新增关键路径提取。
2. 实现机器块相邻交换、块首/块尾插入、候选机器重分配三类邻域。
3. 用 Barnes best-known gap 作为是否有效的判据。

