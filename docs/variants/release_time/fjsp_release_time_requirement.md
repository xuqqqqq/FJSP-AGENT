# Release-Time FJSP 自演进演示需求文档

## 1. 问题目标

给定 Flexible Job-Shop Scheduling Problem with Release Times
（Release-Time FJSP）算例，生成一个完整合法调度方案，使所有工件的所有工序都被安排到可选机器上，并尽量最小化最大完工时间（makespan）。

Release-Time FJSP 与标准 FJSP 的关键差异是：工件和机器不一定都在时间 0 可用。每个工件 `j` 有 release time `r_j`，表示该工件最早可开始加工时间；每台机器 `m` 有 initial available time `a_m`，表示该机器最早可加工时间。这两个时间下界会影响工序开工时间和最终 makespan。

本任务的算例主体沿用标准 FJSP token 格式，机器编号使用 0-based 编号。Release-time 数据位于标准主体之后，由固定 2 行矩阵给出；不存在 SDST setup matrix、维修窗口或其他变种尾部数据。

## 2. 硬约束

- 每个工件由若干道工序组成，同一工件内部必须满足工序先后顺序。
- 每道工序必须从其候选机器集合中选择一台机器加工。
- 工序加工时间必须等于所选机器对应的 processing time；processing time 可以为 0，但不能为负。
- 同一台机器一次只能处理一个工序加工活动，机器上的加工区间不能重叠。
- 对每个工件 `j`，第一道工序必须满足 `start(j, 0) >= r_j`。
- 对每台机器 `m`，任意分配到该机器的工序必须满足 `start >= a_m`。
- 解必须包含所有工序，不能遗漏或重复。
- 若复用标准 FJSP 求解器、解码器或邻域搜索逻辑，必须增加 release-aware 时间传播，不能默认所有工件和机器都在时间 0 可用。

## 3. 优化目标

主要目标是最小化满足 release-time 约束后的 makespan。辅助观察指标包括：

- `max_job_release_time`：最大工件释放时间。
- `max_machine_available_time`：最大机器初始可用时间。
- `runtime_seconds`：候选求解器运行时间。

只有 evaluator 判定合法的解才能参与质量比较。非法解不能因为 makespan 较小而被接受。

## 4. 自演进要求

系统应从本需求文档、IO 文档和 Release-Time FJSP 算例出发，执行固定 evaluator 支撑的自演进循环：

1. 解析 Release-Time FJSP 输入，包括 0-based 标准 FJSP 工序候选部分和尾部 2 行 release-time 矩阵。
2. 生成 release-aware 的调度策略候选。
3. 调用候选 solver 生成加工操作调度。
4. evaluator 根据输入实例中的 `r_j` 和 `a_m` 检查工件释放时间、机器初始可用时间、机器容量、工序顺序、候选机器和加工时长。
5. 汇总 makespan、release-time 指标、合法率和失败原因。
6. 后续代码演进应优先修改 release-aware dispatch / insertion / neighborhood / timing propagation 逻辑，不应随意改变输入输出协议。

Evaluator 是唯一合法性和质量判定来源，模型自评不能替代 evaluator。
