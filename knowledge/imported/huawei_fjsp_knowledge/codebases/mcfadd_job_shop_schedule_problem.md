---
id: codebase-mcfadd-job-shop-schedule-problem
type: codebase
title: mcfadd/Job_Shop_Schedule_Problem
tags: [job-shop, fjsp, tabu-search, genetic-algorithm, sequence-dependent-setup]
source: https://github.com/mcfadd/Job_Shop_Schedule_Problem
status: seed
---

## 仓库定位

该仓库定位为并行 Tabu Search 和 Genetic Algorithm 调度求解，涉及 flexible job shop 和 sequence-dependent setup。虽然它未必适合直接接入 Barnes 标准 FJSP，但对华为工业算例中的顺序相关 setup 有参考价值。

## 可复用模块

1. Tabu Search 主循环。
2. 遗传算法主循环。
3. sequence-dependent setup 的处理方式。
4. 甘特图和实验可视化。

## 输入输出差异

该项目有自己的数据结构和 Cython 扩展，直接复用成本可能较高。更适合做方法参考而不是直接依赖。

## 接入难度

较高。需要检查 Cython 编译、依赖版本、许可证和输入适配成本。

## 后续动作

1. 暂不作为第一批直接依赖。
2. 后续针对华为 setup 优化时，阅读其 setup move 和 tabu 设计。

