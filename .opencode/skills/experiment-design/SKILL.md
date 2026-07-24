---
name: experiment-design
description: 为 AlgoForge Main Agent 设计可证伪的算法改进实验，区分机制激活、诊断性 smoke 与正式 Core evaluator 证据，并据此选择 probe、scale、pivot 或 research_tournament。仅用于 Main 的方向选择、实现规划和轮后复盘，不授权 Coding Worker 扩大实现范围。
---

# AlgoForge 可证伪实验设计

## 触发条件

- AlgoForge Main Agent 需要为一次算法改进轮设计可证伪实验，并据此决定 `probe`、`scale`、`pivot` 或 `research_tournament`。
- 任务处于方向选择、实现规划或轮后复盘阶段，而不是直接授权 Coding Worker 扩大实现范围。

## 读取顺序

1. 先读实例画像、incumbent 能力审计、历史候选和正式 Core 结果。
2. 再按需读取 [pilot-and-falsification.md](references/pilot-and-falsification.md)、[ablation-and-sensitivity.md](references/ablation-and-sensitivity.md) 与 [evidence-and-handoff.md](references/evidence-and-handoff.md)。
3. 最后为所选方法族收敛 `knowledge_query`，不要预先绑定未经检索验证的实现包。

## 执行步骤

1. 把每一轮定义为消除一个关键不确定性的实验，先写清什么结果会改变下一步决定。
2. 写出可证伪假设：目标瓶颈、预期机制、适用范围、替代解释和反证信号。
3. 比较一到三个兼容方法族；只有结构证据支持耦合时才组合多个方法族。
4. 将假设编译为最小连贯变异，明确目标符号、保留项、禁止项、预算、activation checks 与停止条件。
5. 先核对候选是否完成、是否合法、机制是否激活，再解释 Core 结果，并据此选择 `probe`、`scale`、`pivot` 或 `research_tournament`。

## 权限与边界

- 只有正式 Core evaluator 结果决定候选是否优于 incumbent。
- smoke 不能更新 incumbent，Core 分数也不能反推未被观测的机制一定生效。
- activation checks 必须证明代码路径和机制执行，不能拿 makespan、排名或 promotion 充当激活证明。
- Main 只选择假设、方法族、证据要求和下一动作，不写 solver 代码。
- Worker 只执行 `WorkerAssignment`；Main 版 `experiment-design` 不直接交给 Worker，Worker 使用受限的 `fjsp-experiment-design-worker`。

## 交付物

- 一份围绕单一关键不确定性的实验设计。
- 受控的 `knowledge_query`、实现规划、激活证据要求与轮后决策规则。
- 对下一步应 `probe`、`scale`、`pivot` 还是进入 `research_tournament` 的明确判断。

## 验证与停止条件

- 若最终分数无法区分路径、覆盖或成本，先补 instrumentation。
- 若机制未激活，结论通常为 `inconclusive`，不能直接否定方法族。
- 若单一改动已通过正确性与激活检查且额外诊断不再改变提交决策，停止局部试验并进入 Core。
