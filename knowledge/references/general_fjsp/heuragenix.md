# HeurAgenix

## 来源

- 代码：[microsoft/HeurAgenix](https://github.com/microsoft/HeurAgenix)
- 论文摘要：[HeurAgenix on Hugging Face Papers](https://huggingface.co/papers/2506.15196)
- 论文页面：[HeurAgenix OpenReview](https://openreview.net/forum?id=xxSK3ZNAhh)

## 相关要点

HeurAgenix 被描述为一个由 LLM 驱动的超启发式框架：它先演化一组启发式，再根据问题状态在其中进行选择。与一次性生成整个 solver 相比，这更接近我们的目标，因为它把以下环节分开了：

- 启发式生成；
- 启发式评估；
- 启发式选择；
- 面向状态地使用启发式池。

## 对 FJSP Harness Agent 的影响

`harness` 不应把每个生成的 solver 都当成孤立制品。
它应学习一组可复用的 FJSP 策略片段：

- 派工规则；
- 机器选择规则；
- 路线选择规则；
- setup 缩减规则；
- batch 构造规则；
- 局部搜索算子。

未来的选择器可以是简单的 bandit、学习型策略，或基于 LLM 的选择器。第一版实现应把选择器放在受信 validator 路径之外。

## 模块映射

- `Strategy Library`：存储可复用的规则片段。
- `Hypothesis Graph`：跟踪片段的演化与兼容性。
- `Policy Recommender`：为新案例选择 top-k 策略配置。
- `Benchmark Runner`：在同一 evaluator 下比较所选策略。
