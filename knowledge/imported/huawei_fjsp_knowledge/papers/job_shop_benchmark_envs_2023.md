---
id: paper-job-shop-benchmark-envs-2023
type: paper
title: Job Shop Scheduling Benchmark: Environments and Instances for Learning and Optimization
tags: [benchmark, fjsp, jsp, environment, dataset]
source: https://arxiv.org/pdf/2308.12794
status: seed
---

## 方法摘要

该方向关注调度问题的基准环境和实例组织，对构建自演进框架的评估层有价值。它提醒我们：算法生成质量不能只看单个算例，而要在多个标准数据集、不同规模、不同随机种子和统一时间预算下评估。

## 适用问题

适合第三阶段验收中的“公开测试集 + 自行构造变体”的评估设计。

## 核心算法片段

该卡片不提供具体求解算子，主要提供评估框架启发：

1. 标准化实例读取。
2. 统一环境接口。
3. 区分训练集、验证集、测试集。
4. 记录公开已知最优值、下界、运行时间和随机种子。
5. 对学习型算法评估泛化而非只看单算例最优。

## 可迁移到本项目的点

1. 建议把 Barnes 只作为快速冒烟测试，把 Brandimarte/Hurink/DP 等作为扩展测试集。
2. 对 LLM 自演进框架，应报告 10 轮 / 30 分钟内的可行满足率和相对差距，而不是只报告最终最好解。
3. 需要保存每轮候选、每轮失败原因和每轮知识库检索结果。

## 后续动作

1. 设计 `benchmark_manifest.json`，统一管理公开算例路径、已知最优值和时间预算。
2. 把本地 `FJSP-Instance-main` 纳入清单。
3. 建立“训练/验证/测试”拆分，防止 LLM/策略网络过拟合 Barnes。

