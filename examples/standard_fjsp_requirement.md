# 标准 FJSP 求解器自演进需求

## 1. 目标

给定一个标准 Flexible Job-Shop Scheduling Problem（FJSP）算例，由 Coding Agent
生成可独立运行的求解器。求解器必须输出完整合法的调度，并尽量最小化最大完工时间
（makespan）。

## 2. 硬约束

- 每个工件包含按固定顺序执行的若干工序。
- 每道工序必须且只能选择其候选机器中的一台。
- 工序加工时间必须等于算例中该候选机器对应的加工时间。
- 同一台机器上的加工区间不能重叠。
- 输出必须包含全部工序，不能遗漏或重复。
- 本问题不包含 sequence-dependent setup time、运输时间或机器不可用区间。

## 3. 优化目标

主要目标是最小化 makespan。若提供 best-known 数据，额外报告相对 BKS 的 gap；
best-known 数据只能用于评测，不能作为求解器的实例特判或硬编码目标。

## 4. Agent 闭环

1. Main Agent 根据需求、IO、算例画像和历史 Core 证据选择方法方向。
2. Coding Agent 按任务书创建或增量修改独立 solver。
3. 确定性候选预检只检查编译、修改范围和安全边界。
4. 固定 Core parser/validator/evaluator 复验完整调度并计算 makespan。
5. 只有 Core 判定合法且严格提升的候选才能晋升，否则回滚。
6. 同一方向按检查批次执行 Local Trial：具体编译、运行或 validator 错误触发受限修补；合法但未提升时执行有归因的同方向 refinement。
7. 支持续跑的 Worker 在同一方向跨检查批次复用获胜 session，并始终从最佳合法父候选继续；批次大小不限制方向寿命。Main 只能建议换向，用户明确同意后才切换，20 秒无响应默认继续当前方向。

Core 是合法性、目标值和晋升的唯一裁决者，模型自评不能替代 Core。
