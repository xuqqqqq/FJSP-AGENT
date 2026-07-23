# Worker Skill 失败经验待审表

本文只用于人工审核，不属于 `knowledge/`、Domain Pack 或任何 Skill 的可检索输入。未经用户确认的条目不得迁移到 Worker Implementation Skill。

运行时 `round_failure_memory` 也只保留为 `provisional_review_required` 观察，`must_avoid` 不再自动写入失败签名。系统仍可阻止完全相同的 patch 原样重放，但不能据此禁止方法族。

## 证据等级

- **已核验事实**：可由固定源码或原始 Core/evaluator 产物直接复现。
- **局部观察**：事实成立，但只覆盖一个任务、实例、seed 或实现。
- **因果推断**：对原因的解释，尚缺对照、消融或激活证据。
- **拒绝入库**：证据不足、口径不一致或表述过度泛化。

## 待用户审核条目

| ID | 候选经验 | 已核验证据 | 当前判断 | 入库状态 | 还缺什么 |
| --- | --- | --- | --- | --- | --- |
| F-01 | 极小 Beam width 会造成覆盖不足 | 用户提供的参考文件 `fjsp_idle_critical_solver.py` 使用 `BEAM_WIDTH=300`；一次 Agent 生成文件在第 712 行把 `beam_width` 限为 2-3 | 两个数值差异是已核验事实；“小宽度导致目标差”只是因果推断 | 不入库 | 在同一代码、输入、seed、deadline 下比较多个 width，记录逐层 expanded/retained、原 incumbent 路径存活、耗时和正式 Core |
| F-02 | idle-critical Beam 对目标实例有效 | 运行 `outputs/dp18a_high_flex_beam_gpt54_high_20260721_rerun` 中，合法 baseline 为 2230，round 0 合法候选为 2194并晋级 | 这是单实例、单 seed、单实现的局部正式观察 | 不写成通用经验 | 与去掉 Beam、只保留规则组合、不同 width/branch 的消融；至少增加独立实例或冻结复测 |
| F-03 | 参考实现的宽 Beam 应直接作为默认值 | 参考文件确实使用 300，但没有证明该值在不同 operation 数、候选机数和 deadline 下都合适 | 过度泛化 | 拒绝入库 | 建立按层耗时和剩余预算缩放的宽度策略，再与固定值比较 |
| F-04 | 后续多轮无提升说明 Main 没学会或 Coding Worker 太弱 | Web 运行 `outputs/web_runs/20260722_234116_7ee70d5e` 的报告显示 baseline 2948、最终 2936、四个方向；但这只能确认总体改善有限 | 原因属于因果推断，不能由最终分数单独判断 | 不入库 | 逐候选核对 Skill 是否加载、目标代码是否改动、activation checks、候选覆盖、Core 和 competition；区分未实现、未激活、已激活但无效 |
| F-05 | 小 Beam 已经在运行，因此继续放大必然改善 | 同一 Web baseline diagnostics 报告 width 2、expanded 5017、retained 1935、pruned 3082，最终 winner 是 seeded RCL 而非 Beam 入口 | 只能说明 Beam 路径执行过且该入口当次未胜；不能证明宽度是唯一瓶颈 | 不入库 | width/branch/机器 shortlist 的正交或最小消融，以及 winner 路径在哪层被裁剪的 telemetry |
| F-06 | 高柔性实例普遍不适合局部搜索 | 现有实例画像和单次运行不足以建立该普遍结论；换机重插与耦合局部搜索本身也利用柔性 | 当前表述不可证实且可能误导路由 | 拒绝入库 | 按柔性、加工时间离散度、机器负载集中度和关键块结构分层，比较 constructive、coupled local search 及组合方法 |
| F-07 | Semantic Reviewer 通过说明算法实现有效 | Reviewer 只能检查源码与声明契约的一致性，不能替代 activation telemetry 或 Core objective | 证据层级错误 | 拒绝作为性能经验 | 无；应作为实验设计中的永久证据边界，而非失败经验 |
| F-08 | AWLS experiment_memory 中的结论可以直接进入新 Skill | 这些文件包含历史任务观察和解释，尚未逐条追溯原始 diff、激活计数和固定 evaluator | 未审核来源集合 | 不入库 | 对每条 claim 建立源码、运行命令、原始 evaluator、重复性和适用范围记录，再单独提交用户审核 |

## 已允许进入 Skill 的内容

以下内容不是“失败经验”，而是从 IO/可行性不变量或已存在参考实现直接得到的实现合同，因此本轮已进入 references：

- assignment 或 machine order 改变后必须完整重解码。
- 部分排程、未知 CP 状态和解码失败候选不能覆盖合法 incumbent。
- Beam 需要结构指纹、合法下界、deadline 和真实层级计数。
- AWLS/Tabu 必须形成 move 生成、评价、接受、tabu/aspiration、状态更新和独立 incumbent 闭环。
- population 编码必须保持 operation 计数与 eligible machine。
- SDST 前驱变化后必须按活动 IO 契约重算 setup。

这些条目仍须由活动 evaluator 验证具体实现，Skill 只提供代码级不变量和模板，不承诺性能提升。
