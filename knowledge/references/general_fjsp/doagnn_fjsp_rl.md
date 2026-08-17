# 面向 FJSP 强化学习的 DOAGNN

## 来源

- ACM 页面：[Dual Operation Aggregation Graph Neural Networks for Solving Flexible Job-Shop Scheduling Problem with Reinforcement Learning](https://dl.acm.org/doi/10.1145/3696410.3714616)
- OpenReview 页面：[DOAGNN WWW 2025](https://openreview.net/forum?id=AWu0bCMVgR)
- 代码：[thxiwilldoit/DOAGNN](https://github.com/thxiwilldoit/DOAGNN)

## 相关要点

DOAGNN 是一个面向 FJSP 的强化学习方向。它与本项目相关的关键点在于，FJSP 的决策天然具有图结构：

- 工序之间存在前驱约束弧；
- 候选机器构成机器分配备选项；
- 机器冲突形成析取关系；
- 派工动作可以基于图特征进行学习。

## 对 FJSP Harness Agent 的影响

`harness` 不应只把标量启发式参数硬编码进去。
它应为使用结构化 FJSP 状态的学习型策略 worker 留出空间。

对最小可行版本来说，实际落地可以保持克制：

- 在 `Context Packet` 中暴露解析后的 FJSP 状态特征；
- 允许 worker 用关键路径、机器块、工序就绪性、剩余工作量等图概念提出规则修改；
- 后续再允许策略后端输出动作评分。

## 模块映射

- `FJSP Parser`：构建工序-机器候选图。
- `Context Builder`：导出紧凑的图/状态摘要。
- `PolicyWorker`：未来用于 PPO/GNN 风格策略的后端。
- `Evaluator`：无论 policy 类型如何，始终是最终裁判。
