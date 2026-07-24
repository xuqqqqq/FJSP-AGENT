# 启发式演化（EoH）

## 来源

- 论文：[Evolution of Heuristics: Towards Efficient Automatic Algorithm Design Using Large Language Model](https://arxiv.org/html/2401.02051v3)
- 代码：[FeiLiu36/EOH](https://github.com/FeiLiu36/EOH)

## 相关要点

EoH 将大语言模型与 evolutionary computation 结合起来，用于自动启发式设计。对本项目更重要的不是某个具体 heuristic，而是其表示与演化闭环：

- 先用自然语言表示一个 heuristic 思路；
- 再把这个思路变成可执行代码；
- 用外部目标函数评估代码；
- 对有前景的 heuristic 思路进行选择、变异和重组。

## 对 FJSP Harness Agent 的影响

FJSP Harness Agent 应保留“策略优先”的工作流：

1. Worker 编写 `strategy.md`。
2. Worker 编写或修补 solver 代码。
3. Harness 运行固定的 evaluator。
4. Experiment Ledger 记录该策略是改进了还是失败了。
5. Hypothesis Graph 演化的是策略家族，而不只是数值参数。

这与用户要求一致：agent 应能改变规则和算子，而不是仅仅调命令行参数。

## 模块映射

- `Context Packet`：包含当前假设和相关历史策略。
- `CodingWorker`：要求先给出自然语言策略，再写代码。
- `Experiment Ledger`：记录策略谱系和目标指标。
- `Hypothesis Graph`：支持变异、交叉、剪枝和 promotion。
