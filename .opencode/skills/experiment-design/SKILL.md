---
name: experiment-design
description: 为 AlgoForge Main Agent 设计可证伪的算法改进实验，区分机制激活、诊断性 smoke 与正式 Core evaluator 证据，并据此选择 probe、scale、pivot 或 research_tournament。仅用于 Main 的方向选择、实现规划和轮后复盘，不授权 Coding Worker 扩大实现范围。
---

# AlgoForge 可证伪实验设计

把每轮看作减少一个关键不确定性的实验。先说明什么结果会改变下一步决定，再选择足以做出该决定的最小证据。不要为了流程完整机械堆叠试验，也不要把“代码写了”或“合法但未提升”当作机制已经验证。

## 守住证据边界

1. **来源事实**：知识卡、Worker Implementation Skill、参考骨架或历史材料实际写了什么。
2. **静态本地事实**：incumbent 源码审计、符号、控制参数和 patch 实际显示什么。
3. **运行诊断**：activation checks、telemetry 和 diagnostic smoke 实际显示什么。
4. **正式测量**：冻结 TaskContract 下 Core evaluator 的合法性、makespan、耗时和 promotion 结果。
5. **适用性推断**：为什么某机制可能解释当前瓶颈；必须同时给出未知项和替代解释。

只有第四层决定候选是否优于 incumbent。前几层用于选择方向、证明机制是否真正执行和解释失败原因。不得用 smoke 更新 incumbent，也不得用 Core 分数反推未被观测的机制一定生效。

## 三阶段工作流

### 方向选择

- 从实例画像、incumbent 能力审计、历史候选和正式结果中找出最有决策价值的不确定性。
- 写出可证伪假设：目标瓶颈、预期机制、适用范围、替代解释和反证信号。
- 比较一到三个兼容方法族；只有结构证据支持耦合时才组合多个方法族。
- 选择只覆盖所选方法族的 `knowledge_query`，不要预先指定未经检索验证的实现包。

### 实现规划

- 把假设编译为最小连贯变异，明确目标符号、保留项、禁止项、预算和停止条件。
- 为每个候选定义 activation checks；它们必须证明代码路径和机制执行，例如展开量、接受量、候选覆盖或阶段耗时，不能使用 makespan、排名或 promotion 充当激活证明。
- 多候选并行时，让候选在机制或强度上可区分，并共享冻结输入、输出、Core 和 incumbent fallback 契约。
- 多因素改动只有在无法独立成立或交互本身就是假设时才合并；否则拆成可归因候选。

### 轮后复盘

- 先核对候选是否完成、是否合法、机制是否激活，再解释 Core 结果。
- 机制未激活时结论通常是 `inconclusive`，下一步为补 instrumentation 或修正实现，不能据此否定方法族。
- 机制激活且有正式改善时可 `scale`；证据不足但方向仍可区分时 `probe`；机制激活且假设被正式结果稳定反驳时 `pivot`；需要有界跨族比较时才用 `research_tournament`。
- 保留合法 incumbent。失败候选是实验结果，不得包装成晋级，也不得通过更换实例、幸运 seed 或评测口径挽救。

## 选择实验类型

- 最终分数无法区分路径、覆盖或成本时，先补 instrumentation。
- 完整候选代价高或正确性风险高时，先做保持同一代码路径和目标语义的 diagnostic smoke。
- 多个可分因素同时变化且归因会改变决策时，做最小消融。
- 参数邻近变化可能改变结论时，做有依据的小范围敏感性检查。
- 随机性或资源噪声可能跨越支持/反证边界时，按冻结协议复测并报告全部结果，不挑最好一次。
- 单一改动已通过正确性和激活检查，额外诊断不会改变提交决定时，停止局部试验并进入 Core。

按需读取 [pilot-and-falsification.md](references/pilot-and-falsification.md)、[ablation-and-sensitivity.md](references/ablation-and-sensitivity.md) 和 [evidence-and-handoff.md](references/evidence-and-handoff.md)。

## 角色边界

- Main 选择假设、方法族、证据要求和下一动作，不写 solver 代码。
- Coding Worker 自主读取 Harness 精确匹配并授权的 Worker Implementation Skills，只实现 `WorkerAssignment`。
- Worker 的局部运行和 telemetry 是诊断证据；Core evaluator 是唯一正式裁判。
- Main 版 experiment-design 不直接交给 Worker；Worker 使用受 `WorkerAssignment` 约束的 `fjsp-experiment-design-worker` 伴生 Skill。两者都不能扩大 Worker 的 `read_set`、方法族、文件范围或其他 Skill 权限。
