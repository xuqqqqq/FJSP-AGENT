---
id: fjsp-instance-feature-method-router
type: method_selection
title: FJSP 实例特征到方法族的选择路由
tags: [fjsp, method-selection, instance-features, algorithm-selection]
status: curated_reference
---

# FJSP 实例特征到方法族的选择路由

本卡只用于第一阶段方向选择。它不指定某个具体 Method Package，也不向 Coding
Worker 提供实现代码。Main Agent 应先解释“当前实例的主要搜索压力是什么”，再输出
少量 `knowledge_query` 标签，由第二阶段检索实现知识。

## 1. 先区分事实、推断和待验证假设

### 可直接测量的实例事实

- `job_count`、`machine_count`、`operation_count`、`operations_per_machine`。
- `avg_candidate_count`、`flexible_operation_ratio`、候选机数量分布。
- 每道工序候选加工时间的 `duration_spread_ratio` 分布。
- 候选资格在机器间的集中程度，以及理论最小负载是否集中在少数机器。
- 是否存在 sequence-dependent setup、setup 密度及 setup/processing 比例。
- 目标数量、时间预算、是否已有合法 incumbent。

### 只有 incumbent 后才能测量的结构事实

- 关键路径长度、关键机器块数量、最长关键块占关键路径的比例。
- 关键或近关键工序中仍可换机的比例。
- 机器负载不均衡程度。
- 多个入口的 assignment/order 指纹是否实际坍缩。

### 必须写成假设、不能写成事实

- “高柔性一定适合群体算法”。
- “大实例一定不能使用 CP”。
- “关键块长就一定应使用某个具体邻域”。
- “某个 Benchmark 名称通常适合某方法”。

算法选择研究说明实例特征能支持选择，但不存在跨项目固定有效的一组阈值。Main 应
给出证据链和备选方向，不应只返回分类标签。

## 2. 判断主要搜索压力

按下面顺序判断，每轮只选一个主要压力。

### A. 还没有合法基线

优先目标是稳定可行和可解释，而不是堆叠深搜索：

- 选择 `construction`、`initialization`、`decoder`。
- 若实例具有真实机器选择，再加入 `load_balance`。
- 若静态画像已经确认高柔性且加工时间跨度非零，不要只发普通多规则构造；选择
  `constructive_search`，并请求 `high_flexibility`、`idle_gap`、`assignment_regret`、
  `decoder`，使初始 solver 直接建立 earliest-gap 与 assignment-first 基线。
- 只有当构造、解码和输出合同闭合后，才进入局部搜索或群体方法。

### B. 排序压力主导

支持信号：候选机稀疏、候选加工时间跨度小、关键路径被少数长机器块支配。

- 首选 `critical_path`、`critical_block`、`local_search`。
- 已有稳定状态表示且需要跨越局部最优时，再请求 `tabu_search` 或 `ils`。
- 不要把大量机器重分配当主机制。

### C. 分配压力主导

支持信号：柔性工序比例高、候选加工时间跨度明显、理论负载集中或当前负载失衡。

- 首选 `machine_reassignment`、`assignment_search`、`load_balance`。
- 若只允许少量关键工序换机，请求 `cp_sat`、`trust_region` 或
  `assignment_aware_local_search`。
- 仍需配套机器序列插入和完整解码，不能只改机器编号。

#### 高柔性路由修正：默认走 assignment-first playbook，不要直接跳到 Beam

当 `flexible_operation_ratio` 和 `avg_candidate_count` 都高，且候选加工时间跨度非零时，默认
按下面的阶段推进，不把“高柔性”直接翻译成 Beam、随机 portfolio 或浅层换机扫描：

1. 用 `earliest-gap` 替换 tail-append / machine-ready 解码。
2. 对每道工序计算
   `pressure = (candidate_count - 1) * (max_duration - min_duration)`。
3. 高 pressure 工序在 `start-first` 后按
   `assignment_regret = assignment_cost - theoretical_fastest_duration` 选择；低 pressure 工序
   保留剩余链等顺序压力。
4. 已有强构造 incumbent 后，转到 `coupled_local_search`，只在关键/近关键池中做小半径
   `assignment_trust_region`，换机后用 `order_preserving_redecode` 保留 incumbent 机器顺序秩。

第一阶段构造请求 `high_flexibility`、`idle_gap`、`assignment_regret`、`decoder`；第二阶段局部
修复请求 `high_flexibility`、`assignment_trust_region`、`order_preserving_redecode`、
`critical_path`。不要用最佳/次佳完整 score 元组差冒充 assignment regret。

只有在上述构造机制已经通过 activation、仍有明确的构造覆盖缺口，并且候选会实际保留多个
不同部分排程时，才显式请求 `beam_search`。旧 idle-critical Beam 是独立备选方法，不是
高柔性默认路由。详细实现边界见 `high_flexibility_assignment_first_playbook.md`。

### D. 分配与排序强耦合

支持信号：柔性不低且加工时间跨度明显，关键/近关键工序同时存在可信替代机器。

- 首选 `hybrid`、`assignment_aware_local_search`、`critical_block`。
- 可选择完整的自适应局部搜索 Method Package，但必须在第二阶段比较包的适用边界。
- 任务书要同时闭合“换机、插入、解码、接受、记忆、扰动”，不能只实现其中一段。

### E. 多样性压力主导

支持信号：多个构造入口的 assignment/order 指纹相同，或局部搜索反复回到同一关键结构；
且时间预算足以维护多个个体。

- 首选 `population`、`memetic` 或 `multi_start`。
- 群体方法必须使用可行编码、去重和局部改进；预算短时不要只增加种群规模。
- 如果只是缺少一次有界扰动，优先 `ils`，不必直接上完整群体框架。

### F. 精确建模或局部精确修复压力主导

支持信号：规模较小、约束可明确建模、需要界或证明；或者已有好 incumbent，希望只释放
少数关键分配/顺序变量。

- 全局模型请求 `cp_sat`、`exact_method`。
- 大实例的局部修复请求 `trust_region`、`cp_sat`、`hybrid`。
- 必须有 deadline、hint、fallback incumbent 和求解状态检查。

## 3. 变体优先于算法偏好

若存在 setup、transport、batching、reentrant、机器日历或多目标，先确认状态、解码器和
evaluator 是否表达这些约束，再选择搜索方法。标准 FJSP 方法不能仅通过增加一个罚项就
声称支持新变体。

## 4. Main Agent 的第一阶段输出

第一阶段只应输出：

```json
{
  "method_family": "一个主要方法族",
  "primary_search_pressure": "construction|sequence|assignment|coupled|diversity|exact",
  "measured_evidence": ["引用 instance_diagnostics 中的字段和值"],
  "uncertainties": ["还缺少的 incumbent 或运行证据"],
  "alternatives_considered": ["至少一个未选方向及拒绝理由"],
  "knowledge_query": ["2-6 个 Domain Pack 已声明标签"]
}
```

第一阶段不得填写 `method_package_id`。第二阶段读取定向知识和匹配的方法包后，Main 才能
形成完整 `DirectionPlan` 与 `WorkerAssignment`。

## 5. 证据强度规则

- 单个实例的静态画像足以提出方向，不足以证明该方向有效。
- incumbent 结构和历史 promotion/rollback 证据高于静态画像。
- 相同方法族连续合法但未提升时，应换主要假设，而不是只改参数。
- 高柔性只能证明 assignment 空间大；必须分别验证 earliest-gap、精确 regret 和 trust-region
  的 activation，不能用 Beam 状态数、随机入口数或完整 score 元组差代替。
- 文件名、已知答案、历史最佳调度和挑选的 seed 不得参与路由。

## 6. 参考来源

- Chaudhry, I. A., & Khan, A. A. (2015). *A research survey: review of flexible
  job shop scheduling techniques*. DOI: `10.1111/itor.12199`。综述了精确、启发式、
  元启发式和混合方法，支持按问题与求解阶段比较方法族，而不是固定单一路线。
- Seiler et al. (2022). *An algorithm selection approach for the flexible job
  shop scheduling problem: Choosing constraint programming solvers through
  machine learning*. DOI: `10.1016/j.ejor.2022.01.034`。说明 FJSP 的实例特征可用于
  算法选择，但选择应基于训练/测量证据。
- Watson et al. (2022). *Instance space analysis and algorithm selection for
  the job shop scheduling problem*. DOI: `10.1016/j.cor.2021.105661`。支持用实例空间
  与结构特征分析算法适用区域。
- Brandimarte (1993). *Routing and scheduling in a flexible job shop by tabu
  search*. DOI: `10.1007/BF02023073`。经典工作明确了机器分配与工序排序的耦合。
- Mastrolilli & Gambardella (2000). *Effective neighbourhood functions for the
  flexible job shop problem*. DOI:
  `10.1002/(SICI)1099-1425(200001/02)3:1<3::AID-JOS32>3.0.CO;2-Y`。支持围绕
  FJSP 关键结构设计有效邻域。
