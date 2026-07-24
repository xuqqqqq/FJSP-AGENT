# HeurAgenix

## 来源

- 代码：[microsoft/HeurAgenix](https://github.com/microsoft/HeurAgenix)
- 论文摘要：[HeurAgenix on Hugging Face Papers](https://huggingface.co/papers/2506.15196)
- 论文页面：[HeurAgenix OpenReview](https://openreview.net/forum?id=xxSK3ZNAhh)

## 相关要点

HeurAgenix 被描述为一个由 LLM 驱动的 hyper-heuristic 框架：它先演化一组 heuristics，再根据问题状态在其中进行选择。与一次性生成整个 solver 相比，这更接近我们的目标，因为它把以下环节分开了：

- 启发式生成；
- 启发式评估；
- 启发式选择；
- 面向状态地使用 heuristic pool。

## 对 FJSP Harness Agent 的影响

harness 不应把每个生成的 solver 都当成孤立 artifact。
它应学习一组可复用的 FJSP 策略片段：

- dispatching rules；
- 机器选择规则；
- 路线选择规则；
- setup-reduction rules；
- batch 构造规则；
- local search operators。

未来的 selector 可以是简单的 bandit、学习型 policy，或基于 LLM 的 selector。第一版实现应把 selector 放在受信 validator 路径之外。

## 模块映射

- `Strategy Library`：存储可复用的规则片段。
- `Hypothesis Graph`：跟踪片段的演化与兼容性。
- `Policy Recommender`：为新 case 选择 top-k 策略配置。
- `Benchmark Runner`：在同一 evaluator 下比较所选策略。
