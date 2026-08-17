---
name: fjsp-experiment-design-worker
description: 为受控编码代理执行 Main 已批准的 FJSP 可证伪实验，包括最小插桩、激活检查、局部试点、消融开关和完整证据回报。始终受 WorkerAssignment 的方向、文件、命令和预算约束，不自行选择方法族或把局部结果当正式 Core 证据。
---

# FJSP 实验设计执行器

## 触发条件

- Main 已批准一个 FJSP 可证伪实验，并通过 `WorkerAssignment` 下发方向、目标、检查项和预算。
- 需要最小插桩、activation checks、局部 pilot、消融开关或完整证据回报。

## 读取顺序

1. 先读 `WorkerAssignment`。
2. 将其中的假设、目标符号、activation checks、acceptance checks 和停止条件编译为本地实现任务。
3. 需要插桩、局部消融或证据摘要时，再参考 [worker-experiment-template.md](references/worker-experiment-template.md)。

## 执行步骤

1. 明确对照关系：哪些 incumbent 行为必须保持，唯一主要变因是什么。
2. 在改机制前先补最小计数器或阶段耗时，让 activation check 能从真实执行路径读取。
3. 实现变异，不为“好结果”修改 IO、evaluator、实例、seed 或预算。
4. 只运行 assignment 允许的 compile、self-check 与 pilot；Harness smoke 和 Core 仍由外部裁判。
5. 完整回报机制是否激活、计数、错误、耗时、局部结果和未验证项，而不是只报最好值。
6. 命中停止条件时保留合法 incumbent 并如实返回。

## 权限与边界

- `activation check` 证明执行路径，Core 证明正式结果，两者不可互相替代。
- 多因素只有在无法独立实现或交互本身就是假设时才允许耦合；否则用可关闭的最小开关保持可归因。
- 只有 `WorkerAssignment.read_set` 明确授权且证据级别清楚时，才可使用历史失败经验。
- 不扩大方法族、参数搜索、文件范围或其他技能权限。

## 交付物

- 一次受 assignment 约束的实验实现或修补。
- 最小但足够的 activation 证据与局部验证结果。
- 对机制是否被触发、假设是否被当前局部证据支持或反驳的明确说明。

## 验证与停止条件

- 只做足以判断当前假设的最小实验，不机械堆叠所有实验类型。
- 若机制未激活，结论通常应为未定，不得直接否定方法族。
- 一旦命中停止条件或发现局部验证超出 assignment 授权，立即停止扩大实验。
