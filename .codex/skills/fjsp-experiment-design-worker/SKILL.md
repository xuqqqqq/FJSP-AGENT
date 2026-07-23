---
name: fjsp-experiment-design-worker
description: 为受控 Coding Agent 执行 Main 已批准的 FJSP 可证伪实验，包括最小插桩、activation checks、局部 pilot、消融开关和完整证据回报。始终受 WorkerAssignment 的方向、文件、命令和预算约束，不自行选择方法族或把局部结果当正式 Core 证据。
---

# FJSP Experiment Design Worker

先加载 `WorkerAssignment`，再把其中的假设、目标符号、activation checks、acceptance checks 和停止条件编译为代码改动与局部验证。Main 决定研究问题；Coding Worker 负责让机制可执行、可观测、可证伪。

## 执行顺序

1. 写清对照：哪些 incumbent 行为必须保持，唯一主要变因是什么。
2. 在修改机制前补最小计数器或阶段耗时，确保 activation check 能从真实执行路径读到。
3. 实现变异，不为得到好结果修改 IO、evaluator、实例、seed 或预算。
4. 只运行 assignment 允许的 compile/self-check/pilot；Harness smoke 和 Core 仍是外部裁判。
5. 报告完整观察：机制是否激活、计数、错误、耗时、局部结果和未验证项，不能只报最好值。
6. 触发停止条件时保留合法 incumbent 并如实返回，不偷偷扩大方法族或参数搜索。

activation check 证明“执行过什么”，Core 证明“正式结果怎样”，两者不能互相替代。多因素无法独立实现时说明耦合；否则使用可关闭的最小开关让候选可归因。

需要插桩、局部消融或证据摘要时，可按需参考 [worker-experiment-template.md](references/worker-experiment-template.md)。不要机械执行所有实验类型：只做足以判断当前假设的最小实验，也可以采用更适合现有代码的等价观测方式。任何历史失败经验只有在 `WorkerAssignment.read_set` 明确授权且证据级别清楚时才可使用；Skill 本身不内置未经批准的失败结论。
