# 多特性 FJSP 文献与测试矩阵

## 筛选口径

- 必须同时出现两个或更多非标准特征。
- 至少提供公开固定实例、补充材料/代码，或论文中足够明确的实例生成规则。
- `A` 表示固定数据可直接获取；`B` 表示有 benchmark/生成规则但还需二次整理；`C` 表示只适合作为方法参考。

## 候选论文

| 等级 | 论文 | 特征组合 | 算例证据 | 项目用途 |
| --- | --- | --- | --- | --- |
| A | Zheng, Xie (2025), *Research on the Flexible Job Shop Scheduling Problem with Job Priorities Considering Transportation Time and Setup Time*, DOI `10.3390/axioms14120914` | BOM 跨工件前置 + 运输 + 工序准备 | Gitee 公开 T01-T12：`https://gitee.com/zhengchuchu0807/fjsp-jpc-tst` | 首个三特性端到端测试 |
| A | Yan, Wang, Yang (2024), *A Learning-Assisted Bi-Population Evolutionary Algorithm for Distributed Flexible Job-Shop Scheduling With Maintenance Decisions*, DOI `10.1109/TEVC.2024.3400043` | 分布式工厂 + 预防维修决策 + 维修成本/能耗 | IEEE 补充材料；代码仓库 `https://github.com/Rodnelps/LBPEA-code` 可访问 | 下一阶段组合“分布式+维修” |
| A/B | Meng et al. (2022), *An MILP Model for Energy-Conscious Flexible Job Shop Problem with Transportation and Sequence-Dependent Setup Times*, DOI `10.3390/su15010776` | SDST + 运输 + 能耗 | 开放获取论文，明确使用扩展 benchmark cases | 适合 evaluator 与 MILP/CP 交叉核验 |
| B | Defersha, Rooyani (2020), *An efficient two-stage genetic algorithm for a flexible job-shop scheduling problem with sequence dependent attached/detached setup, machine release date and lag-time*, DOI `10.1016/j.cie.2020.106605` | 两类序列相关准备 + 机器释放时刻 + time lag | 论文报告多组 benchmark 与大规模实例，固定文件入口尚待整理 | 检验 3-4 个时间约束联合传播 |
| B | Zhang et al. (2024), *An energy-saving distributed flexible job shop scheduling with machine breakdowns*, DOI `10.1016/j.asoc.2024.112276` | 分布式 + 随机到达 + 机器故障 + 能耗/稳定性 | 论文报告 69 个经典 benchmark 实例 | 动态多特性压力测试 |
| B | Li, Pan, Tasgetiren (2013), *A discrete artificial bee colony algorithm for the multi-objective flexible job-shop scheduling problem with maintenance activities*, DOI `10.1016/j.apm.2013.07.038` | FJSP + 维修活动 + 多目标 | 论文提供实验算例与生成设置 | 对照现有 downtime 语义与维修决策语义 |
| B | Wang et al. (2020), *A novel multi-objective optimization algorithm for the integrated scheduling of flexible job shops considering preventive maintenance activities and transportation processes*, DOI `10.1007/s00500-020-05347-z` | 预防维修 + 运输 + 多目标 | 论文含实验实例，公开固定文件尚待确认 | 很适合第二个静态组合族 |
| B | Zhang, Li, Gong (2024), *Deep reinforcement learning-based memetic algorithm for energy-aware flexible job shop scheduling with multi-AGV*, DOI `10.1016/j.cie.2024.109917` | 多 AGV + 机器调度 + 能耗 | 两套 benchmark、共 20 个实例 | 引入有限运输资源后的测试 |
| B | Luo et al. (2020), *An efficient memetic algorithm for distributed flexible job shop scheduling problem with transfers*, DOI `10.1016/j.eswa.2020.113721` | 分布式 + 跨厂转运 | 40 个 DFJSPT benchmark；本地已有对应论文与数据包 | 已有 distributed-transfer 能力的回归基线 |
| C/B | Gupta, Jain (2021), *Analysis of Integrated Preventive Maintenance and Machine Failure in Stochastic Flexible Job Shop Scheduling with Sequence-dependent Setup Time*, DOI `10.1080/23080477.2021.1992823` | SDST + 预防维修 + 随机故障 | 论文模型与仿真设置可见，固定 benchmark 下载入口未确认 | 随机/仿真 evaluator 研究，不作为首测 |

## 首测选择

选择 Zheng 与 Xie 的 T01，原因是公开固定文件、规模小、三类特征边界清楚，并且可用 CP-SAT 建立独立最优基准。项目规范化文件为 `examples/fjsp_jpc_tst_T01.jpctst.json`，保留论文的 10 工件、4 机器、39 工序、准备规则、运输矩阵与 9 条 BOM 前置边。

独立 CP-SAT 在 8 workers、60 秒上限下返回 `OPTIMAL`，makespan 为 `140`；固定 evaluator 重算结果为 39/39 工序、9 条前置约束 0 违反、运输 0 违反、39 次准备，总准备时间 121。

## 解释边界

- 本首测的 setup 是当前工序/机器相关准备时间，不是 SDST。
- 本首测的 priority 是 BOM 硬前置，不是旧 `fjsp_priority` 的软优先工件目标。
- 后续导入其他论文时必须重新冻结 IO；不得仅凭相同关键词复用 evaluator。
