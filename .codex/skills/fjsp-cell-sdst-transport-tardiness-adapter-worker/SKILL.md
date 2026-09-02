---
name: fjsp-cell-sdst-transport-tardiness-adapter-worker
description: 为已选 FJSP 方法族联合适配单元间运输、族序列相关准备、完整回流路线、交期与总拖期目标。仅在运行时激活 fjsp_cell_sdst_transport_tardiness 时使用。
---

# 单元运输、族准备、回流与拖期联合适配

## 必须保持的规则

1. 解析公开规范化实例中的物理机器副本、cell、part family、due date 和 `jobs[].operations` 中已完整展开的工序路线；数组位置就是独立 `op_id`，不存在必须另读的 `reentrant_route` 字段。
2. 后继工序的最早开始时间包含前序结束与有向 cell 运输时间。
3. 同一物理机器上的相邻工序按前后 family 计算有向 setup；机器副本分别互斥。
4. 回流路线已完整展开，每次访问使用独立 `(job_id, op_id)`。
5. 独立重算 `(makespan, total_tardiness)` 并按严格词典序比较。

## 方法要求

- 构造式搜索使用多起点和互补偏好，不得退化成单一固定派工规则。
- 构造式搜索只需对每个完整候选统一重算、验证并事务式更新 incumbent，不要求实现换机或顺序局部移动。
- 局部搜索联合机器副本重分配与插入位置，完整重解码运输和 setup。
- 群体/模因搜索保留顺序与分配多样性，并进行有界局部改进。
- 精确混合必须真正调用求解器并提取可行解；若只做局部修复，要明确固定变量与邻域边界。

## 验收

只接受固定 evaluator 合法且完整声明 `total_tardiness` 的候选。参考点和论文结果只作只读报告。
