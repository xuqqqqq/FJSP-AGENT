# FJSP-SDST Fattahi 自演进演示需求文档

## 1. 问题目标

给定 Flexible Job-Shop Scheduling Problem with Sequence-Dependent Setup Times
（FJSP-SDST）算例，生成一个完整合法调度方案，使所有工件的所有工序都被安排到可选机器上，并尽量最小化包含切换准备时间后的最大完工时间（makespan）。

FJSP-SDST 与标准 FJSP 的关键差异是：同一台机器上连续加工两道不同工序时，需要根据前一道工序、后一道工序和机器编号插入 sequence-dependent setup time。该 setup time 占用机器时间，影响后续工序开工时间和最终 makespan。

## 2. 硬约束

- 每个工件由若干道工序组成，同一工件内部必须满足工序先后顺序。
- 每道工序必须从其候选机器集合中选择一台机器加工。
- 工序加工时间必须等于所选机器对应的 processing time。
- 同一台机器一次只能处理一个活动。活动包括工序加工时间和相邻工序之间的 setup time。
- 若同一机器上工序 `A` 后紧跟工序 `B`，则 `B.start >= A.end + setup[machine][A][B]`。
- 解必须包含所有工序，不能遗漏或重复。
- AWLS 标准 FJSP 后端当前不作为 FJSP-SDST 的默认求解路径，除非后续明确增加 setup-aware 解码和邻域评价。

## 3. 优化目标

主要目标是最小化包含 setup time 后的 makespan。辅助观察指标包括：

- `setup_time`：被实际机器序列触发的 setup time 总和。
- `setup_count`：触发非零 setup 的相邻工序对数量。
- `runtime_seconds`：候选求解器运行时间。

只有 evaluator 判定合法的解才能参与质量比较。非法解不能因为 makespan 较小而被接受。

## 4. 自演进要求

系统应从本需求文档、IO 文档和 Fattahi SDST 算例出发，执行固定 evaluator 支撑的自演进循环：

1. 解析 FJSP-SDST 输入，包括标准 FJSP 工序候选部分和尾部 setup 矩阵。
2. 生成 setup-aware 的调度策略候选。
3. 调用非 AWLS 候选 solver 生成加工操作调度。
4. evaluator 根据机器序列隐式重算 setup time，并检查机器容量、工序顺序、候选机器和加工时长。
5. 汇总 makespan、setup 指标、合法率和失败原因。
6. 后续代码演进应优先修改 setup-aware dispatch / insertion / neighborhood 逻辑，不应随意改变输入输出协议。

Evaluator 是唯一合法性和质量判定来源，模型自评不能替代 evaluator。
