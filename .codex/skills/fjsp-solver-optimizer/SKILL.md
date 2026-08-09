---
name: fjsp-solver-optimizer
description: 面向柔性作业车间调度（FJSP）的求解器分析与自主优化方法。用于 AlgoForge 需要降低 makespan、诊断搜索停滞、改进机器分配与工序排序、设计关键路径邻域、组合启发式与 CP-SAT、控制时间预算，或检查实例名、已知答案和随机种子过拟合时。
---

# FJSP 求解器优化

## 触发条件

- AlgoForge 需要降低 makespan、诊断搜索停滞、改进机器分配或工序排序、设计关键路径邻域、组合启发式与 CP-SAT、控制时间预算，或检查实例名、已知答案与随机种子过拟合。

## 读取顺序

1. 先读取当前解析器、实例、已有解和正式 evaluator 证据，确认主要瓶颈来自 assignment、sequence、decoder、多样性还是预算分配。
2. 再读取当前轮的假设图、历史失败记录与已验证经验，避免把旧路线换个说法重复提交。
3. 需要具体策略时，按需读取 `knowledge/references/general_fjsp/optimization_playbook.md`、`knowledge/references/general_fjsp/core_pseudocode.md` 与 `knowledge/references/general_fjsp/lessons_and_pitfalls.md`。

## 执行步骤

1. 基于结构证据提出一个可证伪假设，不把多起点、局部搜索或 CP-SAT 无差别叠加。
2. 先区分 foundation 阶段与正式优化阶段：foundation 只负责合法 warm start，不绑定
   `constructive_search`。低柔性/候选稀疏表示 assignment 压力下降、sequence 压力上升，正式
   候选优先比较 critical-block local search、真实 CP-SAT/CP-LNS 与 sequence-oriented memetic；
   是否选 exact 或 population 再由规模、运行环境、预算和激活证据决定。
3. 将方法映射到当前仓库中的具体函数、数据结构或搜索阶段，形成真实代码差异。
4. 用与 baseline 语义一致的局部命令比较改动前后结果，同时检查正确性、耗时和解结构变化。
5. 按结构特征选择研究压力和方法，不按实例名、Benchmark 家族、已知最优值、历史最佳解或挑选过的 seed 路由。
6. 形成可验证研究回路：有界 `WorkerAssignment`、短 smoke、静态门禁、Core evaluator、promotion/rollback 与经验更新。
7. 只有当方向有可信收益时保留差异；无收益则保留失败结论并回退无效差异。

## 权限与边界

- 本 Skill 是领域方法库，不是固定流水线。
- 算法实现属于 Skill、知识库、Method Package 和 Coding Worker；通用后端只负责任务编排、证据与门禁。
- Coding Worker 只读取 `WorkerAssignment`、当前候选代码和被选中的少量知识资产，不得看到完整 Context Packet，也不得自行换方向或方法包。
- 不搜索或保存“最好用”的随机种子，不把下界、已知最优值或历史最佳解注入搜索决策。
- 不增加实例名分支、单实例常量或对 evaluator/trustedFiles 的修改。

## 交付物

- 一个围绕单一可证伪假设的优化方向。
- 对应的代码差异、局部验证结果、风险说明，以及是否应继续 `probe`、`scale`、`pivot` 或 rollback 的判断。
- 结构化诊断，说明当前主要压力来自哪里，以及为何选择当前方法。

## 验证与停止条件

- 只有严格优于 incumbent 且语义完整的候选才能 promotion；合法但不提升、变差或阻断未修复的候选都应 rollback。
- 先区分构造、探索、探测和最终改进的耗时与收益，再决定是否继续。
- 若新路线只是旧失败路线的改写、缺少结构证据支撑，或依赖幸运运行，停止继续扩大该方向。
