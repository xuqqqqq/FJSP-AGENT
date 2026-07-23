---
name: fjsp-population-memetic-worker
description: 为受控 Coding Agent 实现 FJSP 群体、遗传和 memetic 搜索，包括 assignment/order 双层编码、合法交叉变异、结构多样性和有界局部改进。用于 Main 已选择 population_memetic 方法族时。
---

# FJSP Population Memetic Worker

先读取 WorkerAssignment 中获准的 `population`、`genetic`、`memetic`、`diversity` 和 `hybrid` 知识卡。围绕当前 solver 表示设计个体，不能用对象身份或 makespan 不同冒充结构多样性。

设计编码、交叉、变异、替换或局部精修时，可按需参考 [memetic-loop-template.md](references/memetic-loop-template.md)。Coding Agent 可以选择其他编码和演化机制，只要能证明 operation-count、机器资格、可行解码、多样性和独立 incumbent 等不变量。

## 实现原则

- 个体同时表达合法 operation 顺序与机器选择；decode 后才能比较目标。
- 初始化来源应互补，并用 assignment/order 指纹去重。
- 交叉和变异必须保持 operation 计数，机器变异只能选择 eligible alternative。
- 每次解码失败都丢弃或回退父代，不能污染群体和 global incumbent。
- 选择、精英保留、停滞检测、重启和局部精修必须形成实际迭代闭环。
- population、generation、mutation strength 和局部搜索配额按实例规模与实测耗时分配，避免只跑一两代的名义实现。

若同时获准局部搜索 Skill，把 memetic refinement 调用共享的合法邻域实现；若同时获准构造 Skill，把构造入口作为初始群体来源之一。全局最佳可行解必须独立于当前群体保存。

在 assignment 允许时记录每代 unique fingerprints、交叉/变异/解码成功数、局部改进激活、重启和 best trajectory。
