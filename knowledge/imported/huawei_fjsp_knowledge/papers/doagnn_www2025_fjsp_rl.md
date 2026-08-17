---
id: paper-doagnn-www2025-fjsp-rl
type: paper
title: Dual Operation Aggregation Graph Neural Networks for Solving FJSP with Reinforcement Learning
tags: [fjsp, reinforcement-learning, gnn, policy, neural-dispatch]
source: https://openreview.net/forum?id=AWu0bCMVgR
status: seed
---

## 方法摘要

DOAGNN 是 WWW 2025 的 FJSP 强化学习方向工作。其核心思想是把 FJSP 的析取图结构拆成两个操作聚合图，使策略网络能够同时感知工序前后关系和机器竞争关系，再通过强化学习训练派工/调度策略。

## 适用问题

适合研究“拿到新算例后直接输出较好策略”的泛化能力。相比手写启发式，图神经网络 / 强化学习能够学习实例结构特征；相比纯参数推荐器，它可以直接参与动作选择。

## 核心算法片段

1. 将 FJSP 状态表示为图结构。
2. 分别聚合工序链关系和机器竞争关系。
3. 输出候选动作的策略分布。
4. 用最大完工时间等目标构造强化学习奖励。
5. 训练后在新规模/新实例上直接推理。

## 可迁移到本项目的点

1. 当前华为项目里的策略网络更偏“参数推荐”，还没有真正替代动作选择器。
2. 该方向可作为第三阶段的高阶路线：在可信解码器和校验器外壳下，用网络替换 `score_action()` 或 `recommend_policy()`。
3. 对标准 FJSP 可先训练；对工业 FJSP 可用特征扩展表达 setup、维修、组批、转运等约束。

## 风险与限制

1. 需要大量训练实例，当前只有两个工业算例不足以训练泛化网络。
2. 训练复杂度高，不适合作为第一版交付核心。
3. 若动作空间没有硬过滤，RL 容易输出不可行动作，因此必须保留可信动作生成器。

## 后续动作

1. 克隆或下载 DOAGNN 代码，阅读数据格式和训练入口。
2. 用 Barnes/Brandimarte/Hurink 生成训练集，先评估标准 FJSP 泛化。
3. 把本项目的受控动作生成器作为动作掩码，减少不可行动作。

