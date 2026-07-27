# 设备维修时段 FJSP 自演进演示需求文档

## 1. 问题目标

给定 Flexible Job-Shop Scheduling Problem with Machine Availability Constraints
（设备维修时段 FJSP）算例，生成一个完整合法调度方案，使所有工件的所有工序都被安排到可选机器上，并尽量最小化最大完工时间（makespan）。

设备维修时段 FJSP 与标准 FJSP 的关键差异是：机器在给定的时段内不可用（如定期维护、故障修理）。每台机器有若干不可用区间 `[start, end)`，在这些时段内该机器不能加工任何工序。工序要么在不可用区间之前完成，要么在区间之后开工——**不能跨越不可用区间**。

本任务的算例主体沿用标准 FJSP token 格式，机器编号使用 0-based 编号。不可用区间数据位于标准主体之后，由 `K + K×3` 个整数（每行 `machine_id start end`）给出；不存在 SDST setup matrix、release-time 尾部或其他变种数据。

## 2. 硬约束

- 每个工件由若干道工序组成，同一工件内部必须满足工序先后顺序。
- 每道工序必须从其候选机器集合中选择一台机器加工。
- 工序加工时间必须等于所选机器对应的 processing time；processing time 可以为 0，但不能为负。
- 同一台机器一次只能处理一个工序加工活动，机器上的加工区间不能重叠。
- 对尾部每条不可用区间 `(machine_id, start, end)`：该机器的 `[start, end)` 时段内不可加工任何工序。
- 分配在机器 `m` 上的工序 `[s, e)` 必须与所有不可用区间 `[u_start, u_end)` 不重叠：
  ```
  s ≥ u_end  或  e ≤ u_start
  ```
- 解必须包含所有工序，不能遗漏或重复。
- 若复用标准 FJSP 求解器、解码器或邻域搜索逻辑，必须在时间传播中考虑机器不可用区间的阻塞效应。

## 3. 优化目标

主要目标是最小化满足机器可用性约束后的 makespan。辅助观察指标包括：

- `machine_availability_violations`：违反的不可用区间约束数量。
- `total_unavailable_duration`：所有机器不可用时长的总和。
- `runtime_seconds`：候选求解器运行时间。

只有 evaluator 判定合法的解才能参与质量比较。非法解不能因为 makespan 较小而被接受。

## 4. 自演进要求

系统应从本需求文档、IO 文档和设备维修时段 FJSP 算例出发，执行固定 evaluator 支撑的自演进循环：

1. 解析设备维修时段 FJSP 输入——标准 FJSP 工序候选 + 尾部不可用区间列表。
2. 生成 maintenance-aware 的调度策略候选。
3. 调用候选 solver 生成加工操作调度。
4. evaluator 根据不可用区间列表检查：每台机器上的所有工序均不与不可用区间重叠。
5. 汇总 makespan、不可用区间违反数、合法率和失败原因。
6. 后续代码演进应优先修改 maintenance-aware dispatch / insertion / timing propagation 逻辑。

Evaluator 是唯一合法性和质量判定来源，模型自评不能替代 evaluator。

本变种算例来源于公开的 FJSP with Availability Constraints 基准（FJSP-FCR 数据集），共 20 个算例，从 5jobs/6mac/15ops 到 20jobs/15mac/240ops，不可用区间复杂度从简单到碎片化递增。
