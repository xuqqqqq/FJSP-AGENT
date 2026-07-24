---
name: fjsp-constructive-search-worker
description: 为受控 Coding Agent 实现 FJSP 多规则构造、空闲间隙插入、有限前瞻、Beam、多起点和结构去重。用于 Main 已选择 constructive_search 方法族时，要求根据获准知识卡和实例规模自主设计可执行搜索，而不是只写极小占位参数。
---

# FJSP 构造搜索执行器

## 触发条件

- Main 已选择 `constructive_search` 方法族。
- `WorkerAssignment` 授权了 `construction`、`constructive_search`、`beam_search`、`idle_gap`、`initialization`、`multi_start`、`load_balance` 或 `critical_dispatch` 相关知识。

## 读取顺序

1. 先读 `WorkerAssignment`。
2. 读取 assignment 中获准的相关知识卡，并映射到当前 solver 的真实数据结构与预算。
3. 需要设计 Beam、最早间隙或多规则入口时，再参考 `knowledge/references/standard_fjsp/idle_critical_beam_implementation_template.md`。

## 执行步骤

1. 设计同时决定 ready operation、机器与插入位置的构造过程，高柔性实例不能只固定最短机器。
2. 在机器时间轴上实现最早可行空闲间隙，必要时比较尾插与 gap insertion。
3. 为多规则入口建立互补排序压力，并用 assignment/order 指纹去重。
4. 让 Beam 状态保留足够继续合法构造的信息，避免 profile key 丢失关键分配差异。
5. 根据实例规模、候选机器分布、deadline 和实测层耗时确定 width、branch 和入口数。
6. 全程独立维护最佳完整可行解，不让部分状态、估计分或未解码路径覆盖 incumbent。

## 权限与边界

- 不照抄模板的固定结构、评分或宽度。
- 不把占位宽度或名义多起点当作“已实现搜索”。
- 若同时授权局部搜索 Skill，只输出结构不同的可行入口池，不重复实现后续局部改进。

## 交付物

- 一个可执行的构造搜索实现或修补。
- 一组结构不同的可行入口，供后续模块消费。
- assignment 允许时的激活证据：各入口 makespan、每层 expanded/retained/pruned、Beam winner、profile collision、规则耗时和机器 shortlist 分布。

## 验证与停止条件

- 只有在真实执行路径中观察到构造、Beam 或多入口机制激活时，才可声称该方法有效。
- 若预算明显闲置，优先扩大有效状态覆盖；若无法形成完整合法解，停止宣称构造搜索已闭合。
