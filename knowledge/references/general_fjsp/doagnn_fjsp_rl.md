# 面向 FJSP 强化学习的 DOAGNN

## 来源

- ACM 页面：[Dual Operation Aggregation Graph Neural Networks for Solving Flexible Job-Shop Scheduling Problem with Reinforcement Learning](https://dl.acm.org/doi/10.1145/3696410.3714616)
- OpenReview 页面：[DOAGNN WWW 2025](https://openreview.net/forum?id=AWu0bCMVgR)
- 代码：[thxiwilldoit/DOAGNN](https://github.com/thxiwilldoit/DOAGNN)

## 相关要点

DOAGNN 是一个面向 FJSP 的强化学习方向。它与本项目相关的关键点在于，FJSP 的决策天然具有图结构：

- operations 之间存在 precedence arcs；
- candidate machines 形成 assignment alternatives；
- machine conflicts 形成 disjunctive relations；
- dispatching actions 可以基于图特征进行学习。

## 对 FJSP Harness Agent 的影响

harness 不应只把标量 heuristic 参数硬编码进去。
它应为使用结构化 FJSP state 的学习型 policy worker 留出空间。

对 MVP 来说，实际落地可以保持克制：

- 在 Context Packet 中暴露解析后的 FJSP state 特征；
- 允许 worker 用 critical path、machine block、operation readiness、remaining workload 等图概念提出规则修改；
- 后续再允许 policy backend 输出 action score。

## 模块映射

- `FJSP Parser`：构建 operation-machine candidate graph。
- `Context Builder`：导出紧凑的 graph/state 摘要。
- `PolicyWorker`：未来用于 PPO/GNN 风格 policy 的 backend。
- `Evaluator`：无论 policy 类型如何，始终是最终裁判。
