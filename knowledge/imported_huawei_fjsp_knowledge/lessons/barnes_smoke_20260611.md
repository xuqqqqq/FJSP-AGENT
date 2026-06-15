---
id: lesson-barnes-smoke-20260611
type: lesson
title: Barnes smoke test 暴露出弱基线幻觉
tags: [lesson, barnes, benchmark, weak-baseline, best-known-gap]
source: outputs/standard_fjsp_barnes_smoke
status: verified
---

## 实验背景

本项目新增 `scripts/standard_fjsp_benchmark.py`，读取 qimingme/FJSP-Instance 中的 Barnes 系列，并使用构造式启发式和轻量规则演化生成标准 FJSP 解。

## 现象

相对脚本内置的 ECT/SPT/MWKR/MOR 等弱启发式基线，策略池平均提升 10.43%，且 21 个算例全部合法。

但与公开 `Best.csv` 对照后：

1. 平均 gap 为 12.61%。
2. 最大 gap 为 17.97%。
3. 说明构造式派工评分虽然能稳定合法，但离高质量 FJSP 算法还有明显距离。

## 判断

1. 弱基线提升不能作为第三阶段验收主证据。
2. 标准 FJSP 必须引入关键路径邻域、机器序列搜索、候选机器重分配、TS/ILS/VNS/GA 等强搜索结构。
3. 自演进 agent 的知识库检索应优先把“当前 gap 大”映射到“局部搜索/元启发式算子”，而不是继续微调派工权重。

## 对下一轮自演进的约束

1. 每轮必须报告相对 best-known gap。
2. 如果候选只修改派工权重，需降低优先级。
3. 下一批候选至少包含一个完整局部搜索算子。
4. 经验库中应记录“相对弱基线提升”与“相对公开最优差距”两个指标。

