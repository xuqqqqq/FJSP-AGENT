---
name: fjsp-coupled-local-search-worker
description: 为受控 Coding Agent 实现 FJSP assignment 与 machine sequence 耦合局部搜索，包括关键路径/关键块、换机重插、交换插入、VND/ILS/Tabu 接受与重启。用于 Main 已选择 coupled_local_search 方法族时，要求形成可迭代闭环并保留独立 incumbent。
---

# FJSP Coupled Local Search Worker

先读取 WorkerAssignment 中与 `assignment_aware_local_search`、`machine_reassignment`、`assignment_search`、`critical_path`、`critical_block`、`local_search`、`ils` 或 `tabu_search` 相关的获准知识卡。按当前 incumbent 证据选择邻域，不把方法名当作实现完成。

设计关键块、同机移动、换机重插、Tabu/AWLS 接受或权重更新时，可按需参考 [awls-coupled-loop-template.md](references/awls-coupled-loop-template.md)。模板来自项目 AWLS/HGTSA 行为契约和参考求解器，不是强制架构；Coding Agent 可以采用其他能形成完整可验证闭环的实现。

## 状态与评价

- 解显式包含 assignment 和每台机器的 operation order。
- 每次换机、交换、插入或块重排后执行完整 DAG/等价精确解码；发现环或非法状态立即丢弃。
- current state 可为接受策略暂时变差，global feasible incumbent 永不退化。
- tabu key、逆移动、aspiration、停滞和扰动必须进入实际生成、选择、应用和更新路径。

## 邻域闭环

1. 从解码结果提取关键/近关键工序、机器紧弧和关键块。
2. 生成有界但非一次性的同机交换/插入/短块重排，以及柔性工序的替代机器重插。
3. 精确评价可行候选，按 makespan、结构和接受规则选择 move。
4. 接受后更新 current、memory、关键结构和 global best，再继续多轮 VND/ILS/Tabu。
5. 停滞时使用有界扰动或新入口重启；deadline 前始终能返回 global best。

## 规模与证据

参数必须由实例规模、可选机器分布、关键结构和实际耗时决定。若 evaluator 预算大量闲置，扩大迭代、候选覆盖或重启深度；若邻域未激活，不得声称局部搜索有效。

在 assignment 允许时记录各邻域 generated/evaluated/accepted/improved、换机与顺序 move 分布、迭代/重启数、阶段耗时和 best trajectory。若同时获准构造 Skill，只消费其可行入口池并共享同一解码器与 incumbent。
