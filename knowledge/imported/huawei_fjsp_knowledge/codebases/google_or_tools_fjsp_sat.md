---
id: codebase-google-or-tools-fjsp-sat
type: codebase
title: Google OR-Tools flexible_job_shop_sat.py
tags: [fjsp, cp-sat, exact-model, baseline, python]
source: https://github.com/google/or-tools/blob/stable/examples/python/flexible_job_shop_sat.py
status: seed
---

## 仓库定位

OR-Tools 提供 CP-SAT 调度建模示例，可作为标准 FJSP 的精确/约束规划基线。它不是 LLM 自演进算法本身，但非常适合做小规模实例的可行性和最优性参考。

## 可复用模块

1. 可选机器建模：每道工序对应多个可选 `interval`。
2. 机器容量建模：同一机器上的 `interval` 添加 `NoOverlap` 约束。
3. 工件前后关系：同一 `job` 内增加先后约束。
4. 目标：最小化全部工序的最大完工时间。

## 输入输出差异

OR-Tools 示例通常使用 Python 内部数据结构，需要写适配器读取 `qimingme/FJSP-Instance` 文本格式。

## 接入难度

中等。主要难点是：

1. 需要安装 OR-Tools。
2. 大规模或复杂扩展约束下可能求解时间较长。
3. 对华为工业算例中的组批、转运和复杂 setup 需要额外建模。

## 后续动作

1. 先写一个 Barnes 小实例 CP-SAT 基线，时间限制 10-60 秒。
2. 用 CP-SAT 解验证我们的解码器和校验器是否口径一致。
3. 对较小 Barnes 实例记录下界和当前最好可行解，作为自演进的强基线。

