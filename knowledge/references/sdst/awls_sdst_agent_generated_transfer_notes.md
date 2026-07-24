---
id: awls-sdst-agent-generated-transfer-notes
type: method-transfer
title: 面向 Agent 自主生成求解器的 AWLS-SDST 方法迁移
tags: [fjsp, sdst, awls, agent-generated-solver, agent-generated-transfer, method-transfer, critical-block, tabu-search, zi]
status: active
---

# 面向 Agent-Generated Solver 的 AWLS-SDST 方法迁移

## 目的

当 coding worker 需要依据 requirement 和 IO 文档创建或演化一个独立的 agent-generated FJSP-SDST solver 时，应使用这张卡。

这里的内容是从本地 AWLS 衍生 solver 中提炼出的方式指导。它不是 solver 实现，不是完整代码模板，也不是 benchmark 目标。不要拷贝本地 solver 源码、历史 schedule 或实例特定分数。worker 应基于这些思路，在当前 IO 和 evaluator contract 下独立地适配、组合、简化或重写。合适的范围可以是一个可审计算子、若干耦合机制，或一套连贯的完整 AWLS 适配，具体取决于所选方向、incumbent 状态、实例证据和预算。下述分阶段顺序是建议，而不是禁止选择完整方法。

## 迁移边界

对于 agent-generated solver，应迁移 AWLS 的算法结构，而不是照搬平台代码：

- 完整表示：包含 operation-to-machine assignment 和每台机器上的有序 operation sequence；
- setup-aware decoder：每次试探 move 都必须在尊重 job precedence 和 machine setup arc 的前提下，把所有 operations 完整 decode 出来；
- critical-path 与 critical-block 聚焦：local move 应围绕可能影响 makespan 的 operation 生成，而不是任意全对扫描；
- 有界候选窗口：用 AWLS 的 RK/LK 风格窗口，或基于 critical machine 的窗口，缩小目标插入位置；
- tabu 与 aspiration：记住最近被扰动的局部 sequence，但若某个 fully decoded 候选严格改进 incumbent，则允许 tabu move；
- 自适应扰动：只有在合法性和有效 neighborhood 已存在后，才把类似 `zi` 的权重作为候选排序压力使用。

不要把 AWLS 当作平台 backend 代码迁移。不要在 `examples/agent_generated*.py` 运行时中导入 evaluator 内部实现或 `harness_agent` helper。

## 推荐构建顺序

这个顺序是低风险实现路径，不是强制架构。
只要能保持合法性、解释耦合关系并验证结果行为，Agent 可以重排或合并阶段。

1. 先恢复一个合法的、operation-level、setup-aware 的 multi-start constructor。
2. 把最佳 schedule 转成稳定表示：
   `assignment[(job_id, op_id)]` 和 `machine_sequences[machine_id]`。
3. 增加一个 decoder，从该表示重建 start/end time，并拒绝 partial、cyclic、duplicate、missing 或 ineligible 候选。
4. 从 decode 后的 incumbent 中提取 critical operations 和 machine critical blocks。
5. 增加一个同机 critical-block 算子：相邻交换、边界移动或有界插入。只有 full decode 之后才打分。
6. 为具备替代可行机器的 critical operation 增加一个换机插入算子。用 RK/LK 风格窗口或小型 setup-aware insertion window 限定目标位置，然后对候选做 full decode。
7. 只有当 move generator 能稳定地产生合法且改进或持平的候选后，才加入短期 tabu memory。
8. 只有在 critical-block 和 change-machine neighborhood 都具备后，才加入 `zi` 风格的自适应排序。`zi` 应扰动候选顺序，而不是替代 makespan 作为目标。

## Critical-Block Neighborhood 形状

AWLS 风格的同机搜索不应一开始就扫描所有 pair。生成的 solver 可以使用以下紧凑模式：

- 识别至少位于一条 critical path 上，或位于最晚完工机器上的 operations；
- 把同一台机器上连续的 critical operations 分组成 block；
- 先尝试 block 边界处的 move，再考虑大范围随机重定位；
- 对每个候选 sequence，都要 decode 整个 schedule，并要求 operation coverage 完全一致；
- 除非明确实现了有界随机搜索，否则只接受 makespan 的严格改进。

setup time 是 machine arc 的一部分。单独更低的 setup time 只能作为 tie-breaker 或过滤条件，不能证明改进成立。

## RK/LK 风格的换机插入

对于拥有替代可行机器的 operation，不要尝试插入目标机器上的所有位置。独立 solver 可以利用 decode 之后可获得的信息，近似实现 AWLS 的 RK/LK 窗口：

- predecessor readiness：被移动的 operation 不能早于其 job 前驱完成前开始；
- successor tail pressure：会推迟 critical suffix 上 job 后继的 move 风险更高；
- target machine sequence：那些结束时间晚于 predecessor readiness 的 operation 附近位置，通常比更早的位置更相关；
- fallback positions：保留少量边界位置，避免窗口为空或被过度裁剪。

窗口只用于筛选候选。接受分数必须基于考虑 setup-aware machine arc 后 fully decoded makespan。

## Head/Tail 与代理打分

AWLS 使用 head/tail 信息高效排序候选 move。在 agent-generated solver 中，应保持保守：

- 用 earliest start/end 以及可选的 remaining-job tail 估计，对少量 top-k 候选排序；
- 把 setup delta、bottleneck load 或 critical-tail pressure 作为次级排序特征；
- 绝不能让 proxy score 直接凌驾于 decoded makespan 之上；
- 如果 proxy 多次与 decode 质量冲突，应保留 decoder，并调整候选过滤器，而不是改 evaluator contract。

## Tabu 与 Aspiration

只有当 move 质量本身已经合理时，tabu memory 才有意义。

- 为受影响的局部 sequence、被移动的 operation、源机器和目标机器存储简短 key。
- tenure 保持较小，并受实例规模或迭代次数约束。
- 除非 full decode 证明其严格改进 incumbent makespan，否则跳过 tabu candidate。
- 不要利用 tabu 接受 partial schedule、仅 setup 更低的候选，或 operation coverage 不完整的候选。

## `zi` 风格的自适应压力

AWLS 中 `zi` 的有用之处，在于停滞期间的自适应压力，而不是某个神奇公式。对生成型 solver 来说：

- 特征可包括 criticality、近期 move 频率、bottleneck machine、setup-heavy 相邻 arc 和 stagnation count；
- 把扰动应用于合法候选 move 之间的排序；
- 在改进后衰减或重置压力；
- 始终保持 makespan 为主目标，并坚持 full-decoded acceptance。

当搜索已进入平台期时，避免只改一个常数乘子或 critical 标志。没有真实 neighborhood 时，`zi` 只会退化成随机 tie-breaking。

## 需要避免的失败模式

- 在没有独立梳理其 representation、decoder、neighborhood、search control、当前变体语义和 runtime budget 的前提下，机械移植整套 AWLS 实现。若证据支持该范围，允许选择并连贯实现完整方法。
- 重写 parser、evaluator、solution schema 或 benchmark 语义。
- 在独立生成的 solver 文件中导入 backend `harness_agent` 模块。
- 用新的 bounded move 替换已经 promoted 的 constructive skeleton，而不是围绕它增量扩展。
- 在同一个 decoder 中混用 operation id、`(job_id, op_id)` pair 和 schedule dictionary。
- 返回 `[]`、`None` 或 partial schedule，并把它按 makespan `0` 打分。
- 把 LB/UB/BKS、历史运行分数或旧 solution 文件当成 solver 输入。
- 当合同声明目标是 makespan 时，却把总 setup time 作为主优化目标。

## Worker 自检

在提交方案前，worker 应能够说明：

- 迁移的是 AWLS 的哪一条思路，以及保留了哪个现有 incumbent 机制；
- decoder 接受和返回的表示是什么；
- 候选窗口如何设界；
- 每次试探 move 后如何验证完整 operation coverage；
- 为什么当前选择的范围是合适的，它究竟是单个增量算子、混合组合，还是连贯的完整方法适配；
- Core 如何在不改变 IO 或 evaluator 语义的前提下，对该算子做消融。
