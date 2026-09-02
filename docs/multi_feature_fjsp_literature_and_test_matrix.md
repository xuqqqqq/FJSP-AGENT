# 多特性 FJSP 文献与测试矩阵

## 筛选口径

- 必须同时出现两个或更多非标准特征。
- 至少提供公开固定实例、补充材料/代码，或论文中足够明确的实例生成规则。
- `A` 表示固定数据可直接获取；`B` 表示有 benchmark/生成规则但还需二次整理；`C` 表示只适合作为方法参考。

## 候选论文

| 等级 | 论文 | 特征组合 | 算例证据 | 项目用途 |
| --- | --- | --- | --- | --- |
| A | Zheng, Xie (2025), *Research on the Flexible Job Shop Scheduling Problem with Job Priorities Considering Transportation Time and Setup Time*, DOI `10.3390/axioms14120914` | BOM 跨工件前置 + 运输 + 工序准备 | Gitee 公开 T01-T12：`https://gitee.com/zhengchuchu0807/fjsp-jpc-tst` | 首个三特性端到端测试 |
| A | Deliktaş et al. (2021/2024), *Evolutionary algorithms for multi-objective flexible job shop cell scheduling* / *A benchmark dataset for multi-objective flexible job shop cell scheduling*, DOI `10.1016/j.asoc.2021.107890`, `10.1016/j.dib.2023.109946` | 单元间运输 + family-SDST + 显式回流路线 + due date；makespan 与 total tardiness | Mendeley Data DOI `10.17632/rtzby7pv7m.1` 公开 43 个 CSV，含小中大实例、真实工业实例和参考点 | 已接入独立 `fjsp_cell_sdst_transport_tardiness`，首测 Ins.#1 |
| A | Yan, Wang, Yang (2024), *A Learning-Assisted Bi-Population Evolutionary Algorithm for Distributed Flexible Job-Shop Scheduling With Maintenance Decisions*, DOI `10.1109/TEVC.2024.3400043` | 分布式工厂 + 预防维修决策 + 维修成本/能耗 | IEEE 补充材料；代码仓库 `https://github.com/Rodnelps/LBPEA-code` 可访问 | 下一阶段组合“分布式+维修” |
| A/B | Meng et al. (2022), *An MILP Model for Energy-Conscious Flexible Job Shop Problem with Transportation and Sequence-Dependent Setup Times*, DOI `10.3390/su15010776` | SDST + 运输 + 能耗 | 开放获取论文，明确使用扩展 benchmark cases | 适合 evaluator 与 MILP/CP 交叉核验 |
| B | Defersha, Rooyani (2020), *An efficient two-stage genetic algorithm for a flexible job-shop scheduling problem with sequence dependent attached/detached setup, machine release date and lag-time*, DOI `10.1016/j.cie.2020.106605` | 两类序列相关准备 + 机器释放时刻 + time lag | 论文报告多组 benchmark 与大规模实例，固定文件入口尚待整理 | 检验 3-4 个时间约束联合传播 |
| B | Zhang et al. (2024), *An energy-saving distributed flexible job shop scheduling with machine breakdowns*, DOI `10.1016/j.asoc.2024.112276` | 分布式 + 随机到达 + 机器故障 + 能耗/稳定性 | 论文报告 69 个经典 benchmark 实例 | 动态多特性压力测试 |
| B | Li, Pan, Tasgetiren (2013), *A discrete artificial bee colony algorithm for the multi-objective flexible job-shop scheduling problem with maintenance activities*, DOI `10.1016/j.apm.2013.07.038` | FJSP + 维修活动 + 多目标 | 论文提供实验算例与生成设置 | 对照现有 downtime 语义与维修决策语义 |
| B | Wang et al. (2020), *A novel multi-objective optimization algorithm for the integrated scheduling of flexible job shops considering preventive maintenance activities and transportation processes*, DOI `10.1007/s00500-020-05347-z` | 预防维修 + 运输 + 多目标 | 论文含实验实例，公开固定文件尚待确认 | 很适合第二个静态组合族 |
| B | Zhang, Li, Gong (2024), *Deep reinforcement learning-based memetic algorithm for energy-aware flexible job shop scheduling with multi-AGV*, DOI `10.1016/j.cie.2024.109917` | 多 AGV + 机器调度 + 能耗 | 两套 benchmark、共 20 个实例 | 引入有限运输资源后的测试 |
| B | Luo et al. (2020), *An efficient memetic algorithm for distributed flexible job shop scheduling problem with transfers*, DOI `10.1016/j.eswa.2020.113721` | 分布式 + 跨厂转运 | 40 个 DFJSPT benchmark；本地已有对应论文与数据包 | 已有 distributed-transfer 能力的回归基线 |
| C/B | Gupta, Jain (2021), *Analysis of Integrated Preventive Maintenance and Machine Failure in Stochastic Flexible Job Shop Scheduling with Sequence-dependent Setup Time*, DOI `10.1080/23080477.2021.1992823` | SDST + 预防维修 + 随机故障 | 论文模型与仿真设置可见，固定 benchmark 下载入口未确认 | 随机/仿真 evaluator 研究，不作为首测 |
| A/C | Tamssaouet et al. (2022), *Multiobjective optimization for complex flexible job-shop scheduling problems*, DOI `10.1016/j.ejor.2021.03.069` | release dates + fixed unavailability + reentrant flows + SDST + minimum lags + p-batching | Knopp 2017 前序模型的公开实例在 `https://github.com/sebastian-knopp/cjs-instances`；2022 新增 downtime/lag 的完整工业实例未确认公开 | 方法与语义参考；v1 仅接入与现有契约一致的 release/calendar/reentrant 限制子集 |
| A | Kacem, Hammadi, Borne (2002), *Approach by localization and multiobjective evolutionary optimization for flexible job-shop scheduling problems*, DOI `10.1109/TSMCC.2002.1009117` | 标准 FJSP + makespan + 最大机器负荷 + 总机器负荷 | 经典 Kacem 多目标实例被后续研究广泛复用；标准 FJSP 主体可直接按候选加工时长重算三目标 | 已接入 `fjsp_multiobjective_workload`；Core 当前冻结为 makespan-first 词典序，不把论文 Pareto 结果冒充同口径排名 |
| A | Knopp, Dauzère-Pérès, Yugma (2017), *A batch-oblivious approach for Complex Job-Shop scheduling problems*, DOI `10.1016/j.ejor.2017.04.050` | release + due date/weight + family-SDST + 机器批容量；TWT/TWC | `https://github.com/sebastian-knopp/cjs-instances` 公开 30 个固定实例、格式 PDF 和生成器 | 数据 A；完整接入需独立 CJS evaluator，不能直接复用现有 PBPM |
| C | Özpeynirci et al. (2021), *Flexible Job Shop Scheduling Problem with Sequence Dependent Setup Time and Job Splitting: Hospital Catering Case Study*, DOI `10.3390/app11041504` | SDST + job splitting + total flow time | 开放获取论文包含医院配餐案例，但未核验到独立数据包，也未证明仅凭公开正文即可完整重构 | 仅作 total-flow-time 与 job-splitting 方法参考；在公式和实例数据完整核验前不接入 evaluator |

## 其他目标函数方向

| 论文/数据 | 目标与特征 | 证据边界 | 处理决定 |
| --- | --- | --- | --- |
| Lei et al. (2023), *Large-Scale Dynamic Scheduling for Flexible Job-Shop With Random Arrivals of New Jobs by Hierarchical Reinforcement Learning*, DOI `10.1109/TII.2023.3272661` | dynamic/random arrival；后续文献把 total flow time 列为性能指标 | 尚未核验 total flow time 是否为论文正式优化目标，也未找到稳定公开数据入口 | 不接入，只保留检索线索 |
| Nie et al. (2013), *A GEP-based reactive scheduling policies constructing approach for dynamic flexible job shop scheduling problem with job release dates*, DOI `10.1007/s10845-012-0626-9` | release dates；makespan、mean flow time、mean tardiness | 不是 total flow time 目标，公开实例入口未核验 | 可用于动态调度规则研究，不复用为当前 total-flow evaluator |
| Chang et al. (2022), *Deep Reinforcement Learning for Dynamic Flexible Job Shop Scheduling with Random Job Arrival*, DOI `10.3390/pr10040760` | random arrival；earliness/tardiness penalty | 论文为 CC BY 4.0，数据/代码公开于 `https://github.com/changjingru/DDQN-for-DFJSP`；目标不是 total flow time | 作为后续“动态到达 + 交期惩罚”高复现候选，必须建立独立目标契约 |

## 已实现的两类研究变体

选择 Zheng 与 Xie 的 T01，原因是公开固定文件、规模小、三类特征边界清楚，并且可用 CP-SAT 建立独立最优基准。项目规范化文件为 `examples/fjsp_jpc_tst_T01.jpctst.json`，保留论文的 10 工件、4 机器、39 工序、准备规则、运输矩阵与 9 条 BOM 前置边。

独立 CP-SAT 在 8 workers、60 秒上限下返回 `OPTIMAL`，makespan 为 `140`；固定 evaluator 重算结果为 39/39 工序、9 条前置约束 0 违反、运输 0 违反、39 次准备，总准备时间 121。

`fjsp_jpc_tst` 现在明确标记为装配/BOM 型独立研究变体。它不是现有软优先级、跨厂转运或 SDST 三项能力的组合。

第二类 `fjsp_calendar_reentrant` 依据 Tamssaouet 等的复杂 FJSP 联合时序，但 v1 只组合项目现有定义完全兼容的 job release、machine initial availability、fixed downtime 和确定性连续回路展开。仓库 smoke 数据仅作语义验证；在获得公开的 2022 完整实例前，不声明论文 benchmark 性能。

第三类 `fjsp_multiobjective_workload` 使用标准 FJSP 可行域，并由固定 evaluator 重算 `makespan`、`max_machine_workload` 和 `total_workload`。正常规模验证文件基于公开 Brandimarte Mk01 原始主体，仅增加 `.mofjsp` 激活标记。当前平台以严格词典序比较三目标；这与 Kacem 论文的 Pareto 优化用途不同，因此只复用目标定义和搜索机制，不直接比较 Pareto 解集质量。

第四类 `fjsp_cell_sdst_transport_tardiness` 直接规范化 Deliktaş 数据集的公开 `Ins.#1.csv`，保留 4 个工件、6 个物理机器副本、13 道完整路线工序、2 个 cell、2 个 part family、有向运输矩阵、有向 family setup 矩阵和 due date。固定 evaluator 联合重算物理机器互斥、运输、setup、makespan 与 total tardiness。独立 CP-SAT 在 8 workers、60 秒上限内证明词典序最优 `(28, 34)`；数据论文给出的单目标 ideal makespan 同为 `28`。论文研究 Pareto 前沿，平台只把 `(makespan, total_tardiness)` 严格词典序作为可复现 Agent 竞争口径。

## 解释边界

- 本首测的 setup 是当前工序/机器相关准备时间，不是 SDST。
- 本首测的 priority 是 BOM 硬前置，不是旧 `fjsp_priority` 的软优先工件目标。
- Knopp 2017 的公开 CJS 文件使用 operation family、recipe、family-to-family setup 和批机容量，不能直接当作当前 `fjsp_pbpm` 或 `fjsp_reentrant` 输入。
- Tamssaouet 2022 的 recipe/size 相关约束需建立独立 schema；当前 `fjsp_min_time_lag` 和 `fjsp_pbpm` evaluator 不得复用。
- Yan/Wang/Yang 的 LBPEA 维修是由编码插入的 PM/CM 决策，实例文件没有固定 downtime；它不能映射为当前 `machine_availability`，工厂也是 job-level 决策且没有跨工序运输，不能映射为当前 `distributed_transfer`。
- Deliktaş 的 cell 不是当前 distributed factory：运输使用实例给定的有向 cell 矩阵，机器副本是独立资源，setup 由前后 part family 决定，因此必须使用独立 evaluator。
- 后续导入其他论文时必须重新冻结 IO；不得仅凭相同关键词复用 evaluator。
