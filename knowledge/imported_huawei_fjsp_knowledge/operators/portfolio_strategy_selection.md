---
id: operator-portfolio-strategy-selection
type: operator
title: 多策略 Portfolio 选择
tags: [operator, portfolio, dispatch-rule, auto-evolution]
source: outputs/standard_fjsp_barnes_smoke
status: verified
---

## 作用

同一派工规则不一定适合所有实例。Portfolio 方法为每个实例运行多条候选策略，并选择通过校验且目标值最好的解。

## 输入

1. 策略池：启发式规则、演化策略、随机种子组合。
2. 算例集合。
3. 外部校验器。

## 输出

每个算例对应的最优合法解及其策略来源。

## 约束安全性

Portfolio 不改变单个求解器的合法性。每条候选解都必须独立通过校验器。

## 本项目证据

在 Barnes smoke test 中：

1. 单一全局演化策略相对弱基线平均比值为 0.9254。
2. 加入 portfolio 后，相对弱基线平均提升 10.43%。
3. 但相对公开 best-known 仍有 12.61% gap。

## 适用阶段

适合作为自演进框架的外层选择机制，也适合与 LLM 生成的差异化候选规则结合。

## 失败模式

1. 如果策略池高度同质化，收益有限。
2. 如果只和弱基线比较，容易高估算法质量。
3. 如果策略池过大，运行时间可能超出验收预算。

