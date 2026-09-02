---
id: fjsp-cell-sdst-transport-tardiness-semantics-and-search
type: reference
title: 单元运输、族准备、回流与拖期联合搜索
tags: [fjsp_cell_sdst_transport_tardiness, cell_transport, family_sdst, reentrant_route, total_tardiness, multi_feature]
status: active
---

# 联合语义

这类公开 FJCS-SDFSTs-ITTs 实例不能拆成几个后处理检查。机器选择会决定 cell 与运输等待，机器顺序会决定 family setup，二者共同改变完工时间和拖期。

规范化 `.fjcs.json` 已把公开数据中的完整回流路线展开为每个 job 的
`operations` 数组。数组位置就是 `op_id`，因此重复访问同一逻辑机器仍是两个
不同工序。Solver 必须逐项读取并保留这些身份，但不得虚构或要求额外的
`reentrant_route`、循环次数或路线展开字段。

## 构造

- 就绪时间必须包含前序工序结束和 cell 间运输。
- 在物理机器的可行插入位置计算前驱 family 到当前 family、当前 family 到后继 family 的 setup 增量。
- 同时维护 makespan 压力和 due-date 紧迫度，多起点至少包含关键路线、最早完工和拖期增量三类偏好。
- 每个完整构造候选先按同一联合解码语义重算和验收，失败候选不能覆盖 incumbent；构造方法不必为了满足通用契约而伪装成局部搜索。

## 搜索

- 换机移动必须联合选择物理机器副本和插入位置，并完整重算受影响工件运输链及机器 setup 链。
- 顺序移动优先覆盖关键块、迟到工件和 family setup 高代价弧。
- 回流路线中的每次访问已经是 `operations` 中独立的 `(job_id, op_id)`，不能因逻辑机器相同而合并。
- 种群编码应同时保持工序顺序和机器副本分配的多样性；局部改进使用严格词典序比较。

## 精确混合

小实例可用 CP-SAT 建模验证：候选机器 optional interval、物理机器 no-overlap、同机顺序布尔变量上的 family setup、相邻工序运输，以及 `T_j >= C_j-d_j`。较大实例优先使用固定分配或小邻域 CP repair，不要求全局证明。

## 禁止退化

- 不能把 cell 当作工厂并复用固定 30/60 的跨厂运输。
- 不能把 family setup 当作工序固定前置 setup。
- 不能只优化 makespan 而遗漏输出和重算 total tardiness。
- 不能把公开参考点当作 solver 输入。
