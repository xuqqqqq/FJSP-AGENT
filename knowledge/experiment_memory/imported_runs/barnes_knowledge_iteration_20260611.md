# Barnes 标准 FJSP 十轮知识驱动迭代记录（2026-06-11）

## 实验边界

- 数据集：`fjsp.barnes.*.txt`，共 21 个标准 FJSP 实例。
- 评估目标：最小化 makespan，并与 `outputs/standard_fjsp_barnes_smoke/Best.csv` 中的 best-known 值计算 gap。
- 初解口径：禁止读取历史排程作为初解；所有轮次都从原始实例文件重新构造初解。
- 允许跨轮学习的信息：仅允许使用上一轮的指标表现选择下一轮配置，不允许把上一轮解结构注入下一轮。
- 输出目录：`outputs/standard_fjsp_knowledge_iter_10rounds_full_v2`。

## 十轮结果

| 轮次 | 策略族 | 平均 gap | 可行率 | warm start |
|---|---|---:|---:|---:|
| adaptive_deepen_both | 组合邻域 + fresh restarts | 9.648% | 1.000 | 0 |
| adaptive_both_restart_crosscheck | 组合邻域 + 多样初解 | 10.190% | 1.000 | 0 |
| tabu_both_fresh_restarts | 组合邻域 + 2 次 fresh restart | 11.608% | 1.000 | 0 |
| tabu_both_more_diverse_initials | 组合邻域 + 更多随机初解 | 12.399% | 1.000 | 0 |
| tabu_n8_deeper_sequence_repair | N8 同机重排加深 | 12.558% | 1.000 | 0 |
| dispatch_portfolio_evolution | 构造式规则组合演化 | 13.046% | 1.000 | 0 |
| tabu_n8_same_machine_order | N8 同机重排 | 13.618% | 1.000 | 0 |
| tabu_both_balanced | 组合邻域基础版 | 14.176% | 1.000 | 0 |
| tabu_k_more_diverse_initials | k-insertion + 更多随机初解 | 16.567% | 1.000 | 0 |
| tabu_k_insertion_machine_change | k-insertion 单独换机 | 20.351% | 1.000 | 0 |

## 有效经验

1. 组合邻域优于单一邻域。N8 负责修复同机顺序，k-insertion 负责路径/机器选择，二者组合后才稳定优于构造式派工。
2. fresh restart 有实际收益。第 7 轮在不使用历史解的前提下，仅通过独立重构初解，把平均 gap 从第 5 轮的 12.399% 进一步压到 11.608%。
3. 自适应加码有效。根据前 8 轮指标选择组合邻域方向，并增加 `iterations`、`initial_random`、`restarts` 后，第 9 轮达到 9.648%，是本次十轮最好结果。
4. 可行性链路稳定。十轮所有实例可行率均为 1.000，未出现校验错误。

## 失败或可疑经验

1. k-insertion 单独使用表现很差。两轮 routing-only 实验分别为 20.351% 和 16.567%，说明当前换机插入的实现没有充分体现论文中“关键路径变换 + 路径重连/交叉”的效果。
2. 基础组合邻域并不天然优于派工演化。第 2 轮 14.176% 低于派工轮 13.046%，说明本地搜索的初始状态、接受准则和邻域排序还需要共同调。
3. 时间预算影响明显。3 秒/实例约束下，第 9 轮仍能改进，但很多实例只完成约 30 次迭代；如果用于论文或验收，需要报告时间预算并做统一对照。
4. 2026-06-15 的 HGTSA-lite 单例验证显示，按谢晋论文卡片直接实现的 N8/k-insertion 候选仍未超过既有 `combined` 邻域。在 `fjsp.barnes.mt10xx.m12j10c3.txt`、seed 0、2 秒预算下，`combined` 为 10.893% gap，保护后的 `hybrid` 为 12.963% gap，纯 `hgtsa-lite` 为 18.301% gap。该结果说明近似评价和候选配额仍需训练或继续人工设计，不能仅凭论文术语替换主策略。

## 下一轮优先方向

1. 修正 k-insertion 的候选排序：不应只按解码后 makespan 贪心接受，可加入关键路径长度变化、机器负载变化、候选机器加工时长变化作为二级评分。
2. 增加 path-relinking 或遗传式 machine-assignment 交叉：当前 fresh restart 只是多启动择优，没有显式吸收不同初解之间的机器分配优势。
3. 把知识选择做成可插拔策略：让每轮配置由知识卡中的 `operators`、`failure_modes`、`next_actions` 字段生成，而不是固定写死在 runner 中。
4. 继续禁止历史解 warm start，以保证实验能代表从文档/知识/原始实例出发的自演进能力。
5. 对 HGTSA 系列算子继续做“短名单近似评价 + 主动解码复核”的两级搜索，优先学习 proxy 权重和候选配额，而不是扩大完整解码数量。
