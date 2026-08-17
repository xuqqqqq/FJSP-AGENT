# AWLS-SDST 适配笔记

这张卡用于提供 agent 上下文，不用于修改 evaluator 语义。

## 来源指针

- AWLS/FJSP local search："An Effective Local Search Algorithm for Flexible Job Shop Scheduling Problem" 描述了用于 FJSP 的 adaptive weighting-based local search（AWLS）、tabu search 和 operation weights。
- FJSP-SDST：Shen, Dauzère-Pérès, and Neufeld (2018), "Solving the flexible job shop scheduling problem with sequence-dependent setup times"，提出了用于 SDST makespan 最小化的析取图思路和 tabu neighborhood。
- 带 setup time 的 FJSP：González, Vela, and Varela, "An Efficient Memetic Algorithm for the Flexible Job Shop with Setup Times", ICAPS 2013，与 HUdata setup benchmark family 相关。
- NS4S IJCAI 2025 报告了在 20 个 SDST-HUdata 实例上、30 秒 FJSP-SDST cutoff 下的实验。

## 执行模式规则

- 平台侧的参考验证可以复用 `harness_agent.domains.io`，但通用编排绝不能导入或执行具体方法实现。
- 独立的 agent-generated solver 必须在生成的产物内部自行实现当前 IO 派生的 parser、setup query 和输出写入器。它不能导入 `harness_agent` 或 evaluator 内部实现。
- 两种模式都必须保持冻结的 evaluator 语义。绝不要在后端自身里再创建第二套 parser。

## 代理优先的阶段顺序

1. 先让 AWLS 的时间传播变成 setup-aware，并与 evaluator 语义一致。
2. 再适配同机 N7/N8 和换机 NK/RK/LK 的 move scoring。
3. 然后把 setup-aware 特征暴露给 `zi` 演化。
4. 只有这些都通过 smoke test 后，agent 才应运行 HUdata 子集或完整 benchmark。
