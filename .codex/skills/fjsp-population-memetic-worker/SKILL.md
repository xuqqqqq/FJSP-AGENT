---
name: fjsp-population-memetic-worker
description: 为受控 Coding Agent 实现 FJSP 群体、遗传和 memetic 搜索，包括 assignment/order 双层编码、合法交叉变异、结构多样性和有界局部改进。用于 Main 已选择 population_memetic 方法族时。
---

# FJSP 群体与 Memetic 执行器

## 触发条件

- Main 已选择 `population_memetic` 方法族。
- `WorkerAssignment` 授权了 `population`、`genetic`、`memetic`、`diversity` 或 `hybrid` 相关知识。
- 低柔性实例仍有巨大的机器顺序组合空间；若预算允许维护多个 sequence basin，本方法族可作为
  正式 challenger，个体差异应主要来自 machine-order 指纹而不是强行制造换机差异。

## 读取顺序

1. 先读 `WorkerAssignment` 中获准的群体搜索知识卡。
2. 围绕当前 solver 表示设计个体，不把对象身份或 makespan 差异当作结构多样性。
3. 需要编码、交叉、变异、替换或局部精修模板时，再参考 `knowledge/references/standard_fjsp/memetic_search_loop_template.md`。

## 执行步骤

1. 设计同时表达合法 operation 顺序与机器选择的个体表示。
2. 用互补初始化来源生成群体，并以 assignment/order 指纹去重。
3. 实现保持 operation 计数与机器资格的交叉和变异。
4. 对每个候选先解码，再比较目标；解码失败时丢弃或回退父代。
5. 形成选择、精英保留、停滞检测、重启和局部精修的真实迭代闭环。
6. 按实例规模和实测耗时分配 population、generation、mutation strength 和局部搜索配额。

## 权限与边界

- 不能用名义上的“跑过几代”冒充群体搜索已实现。
- 若同时授权局部搜索 Skill，只调用共享的合法邻域实现做 memetic refinement。
- 若同时授权构造 Skill，可把构造入口作为初始群体来源之一。
- `global incumbent` 必须独立于当前群体保存。
- 不要求外部先提供高质量 incumbent；foundation warm start 可作为一个种子，其余个体应由
  合法 sequence-oriented 初始化、交叉、变异和局部改进产生。

## 交付物

- 一个可执行的 population/memetic 搜索闭环。
- assignment 允许时的激活证据：每代 unique fingerprints、交叉/变异/解码成功数、局部改进激活、重启和 best trajectory。

## 验证与停止条件

- 只有在解码、选择、重启和局部精修都进入真实迭代路径时，才可声称方法已闭合。
- 若多样性、合法解码或独立 incumbent 无法保持，停止扩大群体搜索主张。
