---
name: fjsp-release-time-adapter-worker
description: 为已选 FJSP 方法族适配静态作业释放时间与机器初始可用时间。
---

# 释放时间 FJSP 适配器

## 契约

- 解析恰好两行、宽度均为 `max(job_count, machine_count)` 的尾部数据，并校验 `-1` 填充。
- 作业 `j` 的第一道工序不能早于 `r_j` 开始。
- 落在机器 `m` 上的每道工序都不能早于 `a_m` 开始。
- 保持固定的 CLI 和 `standard_fjsp_schedule_v1`；不要修改 parser/evaluator Core。

## 实现

1. 用 `r_j` 初始化作业就绪时间，用 `a_m` 初始化机器就绪时间。
2. 在每次完整解码、空隙插入、move 评估和 incumbent 校验中，都必须应用这两个下界。
3. 在局部搜索或种群搜索中，move 表示可以不显式携带时间，但每个被接受的候选都必须做完整重解码。
4. 对 CP-SAT，加入 `start[j,0] >= r_j` 以及每个被选中机器候选的起始下界。
5. 用非零释放时间值和一个被拒绝的违规排程来报告激活证据。

编辑前先阅读两张释放时间知识卡和已分配的方法包。
