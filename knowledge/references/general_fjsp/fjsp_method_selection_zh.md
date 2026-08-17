---
id: general-fjsp-method-selection-zh
type: method_selection
title: FJSP 方法选择卡（中文）
tags: [fjsp, method-selection, constructive, local-search, tabu-search, ils, population, cp-sat, hybrid]
status: curated_reference
---

# FJSP 方法选择卡（中文）

这张卡只回答“当前任务更适合哪一类方法”，不提供任何具体算法代码、邻域实现或
参数模板。用途是一阶段给 Main 做方法适用性判断，避免在还没确认方向时就把某个
专用算法骨架直接塞给 worker。

## 先看问题形态

优先判断四件事：

1. 实例规模是否足够大，大到精确方法难以在预算内收敛。
2. 约束是否复杂，例如 setup time、transport、batching、reentrant、机器日历。
3. 目标是否单一，例如只优化 makespan，还是还要兼顾能耗、延期、稳定性。
4. 当前阶段是要快速出可行基线，还是要在已有可行解上继续深挖。

随后必须读取 `fjsp_instance_feature_method_router.md`，用实际
`instance_diagnostics` 区分排序、分配、耦合、多样性和精确建模压力。仅凭“实例大”或
“平均候选机多”不足以锁定方法。

## 方法族对比

| 方法族 | 适用场景 | 优势 | 主要风险 | 何时优先 |
| --- | --- | --- | --- | --- |
| 构造启发式 / 构造式束搜索 | 需要先快速得到稳定可行解；工程上需要强可解释性；或已有明确束搜索覆盖假设 | 可用空闲间隙、规则组合或有界多状态搜索形成分配与顺序 | 规则上界可能一般；束宽和分支设计不当会耗时或过早剪枝 | 首轮建基线；需要热启动解；assignment-first 已激活但仍有明确构造覆盖缺口 |
| 局部搜索 / 禁忌 / ILS | 已有可行解，希望继续压 makespan；单目标或少量目标 | 改进能力强，工程实现边界清楚，适合围绕关键路径/机器块做迭代 | 很依赖解表示、邻域质量和增量评估；容易过拟合少数实例 | 有稳定基线、需要中短时预算内持续改进 |
| 群体方法 / GA / DE / PSO / 蚁群等 | 解空间粗糙、需要更强多样性；想同时探索多种编码/调度偏好 | 全局探索更强，适合与局部搜索做混合 | 编码设计和可行性维护复杂，预算不足时波动大 | 需要多样性、允许较长预算、可承受更多调参 |
| CP-SAT / MILP / 精确方法 | 规模较小或结构规整；需要高质量证明或强下界 | 对小中型实例可能直接给出很强结果；可做精确校验 | 大规模 FJSP 常很快变慢；复杂变体建模成本高 | 小规模、高价值实例；需要验证建模正确性 |
| 混合方法 | 问题既大又复杂；既要初解速度也要后续改进质量 | 可以把构造、局部搜索、群体、CP-SAT 的强项拼起来 | 设计复杂，接口不清时容易变成“什么都混一点” | 已确认单一方法不足，且团队能维护清晰模块边界 |

## 方向选择建议

### 1. 先出可行解

先用共享基础能力中的 ready-list 构造或简单规则组合得到热启动解。这里是在完成
parser/decoder/合法 incumbent 合同，不是在选择正式优化方法族。关键不是“第一次就很强”，而是：

- 可行性稳定；
- 输出格式稳定；
- 便于后续局部搜索接管。

基线一旦合法，应立即根据实例压力重新比较方法族，不能因为热启动解由构造器产生就
继承 `constructive_search`。低柔性实例通常是顺序优先：优先比较关键块 Tabu/ILS、
表达机器顺序的 CP-SAT/CP-LNS，以及预算允许时的面向顺序的 memetic。

### 2. 已有可行解，目标是继续降 makespan

优先考虑局部搜索、禁忌、ILS，尤其适合：

- 关键路径信息明确；
- 机器冲突是主要瓶颈；
- 可以围绕少数高价值 move 反复迭代。

### 3. 高柔性，但还没有局部瓶颈证据

不要仅因为候选机器多，就直接做浅层 assignment local search，也不要默认跳到束搜索。高柔性且
候选加工时间跨度非零时，先建立先分配后排序的构造基线：使用 earliest-gap，在 start-first
之后按精确定义的
`pressure = (candidate_count - 1) * duration_span` 区分工序；高 pressure 工序再按
`assignment_regret = assignment_cost - theoretical_fastest_duration` 细化机器选择，低 pressure
工序继续保留剩余链等顺序压力。请求标签为 `constructive_search`、`high_flexibility`、
`idle_gap`、`assignment_regret`、`decoder`。最佳/次佳完整 score 元组差不属于 assignment regret。

构造机制激活并得到稳定 incumbent 后，若关键或近关键瓶颈已经可定位，再选择
`coupled_local_search`，请求 `high_flexibility`、`assignment_trust_region`、
`order_preserving_redecode`、`critical_path`：只做小半径换机，换机后保留 incumbent 的机器顺序秩
重解码。只有上述机制已激活但仍有明确构造覆盖缺口，且候选真正保留多个不同部分排程时，
才把 `beam_search` 作为独立备选方向。

### 4. 约束复杂，单一局部邻域不够

优先考虑混合方法。典型思路是：

- 构造法负责快速给出可行初解；
- 局部搜索负责精修；
- 群体方法或 CP-SAT 只承担局部子问题、重启或校准角色。

### 5. 需要多目标或鲁棒性

单纯“把原来的局部搜索多跑几轮”通常不够。更适合：

- 能明确区分主目标/次目标的混合框架；
- 或能自然保留多样性的群体方法。

## 约束复杂度与方法偏好

| 特征 | 更偏好的方法族 | 备注 |
| --- | --- | --- |
| 仅标准 FJSP、目标单一、需要快 | 构造启发式 + 轻量局部搜索 | 先求稳，再决定是否加深搜索 |
| 标准 FJSP、高柔性、缺少稳定关键结构 | earliest-gap + pressure/regret 先分配后排序构造 | 先验证精确 regret；有关键/近关键证据后再进入 trust-region 与保序重解码 |
| 有 setup time / 顺序相关代价 | 局部搜索或混合方法 | 需要把序列变化的影响纳入评价，单纯规则法常不够 |
| 有 batching / transport / reentrant | 混合方法 | 状态表示和可行性维护通常比标准 FJSP 更难 |
| 小规模但质量要求高 | CP-SAT / 精确 + 启发式热启动 | 精确方法可当主方法或校验器 |
| 预算长、希望保留多样策略 | 群体方法 + 局部搜索 | 群体负责探索，局部搜索负责强化利用 |

## 不要过早下结论的信号

如果出现下面情况，不要直接锁定某个具体算法名：

- 任务合同还没确认实例变体；
- 只知道“要更强”，但不知道要更快、还是更稳、还是更优；
- 已有求解器失败原因还没定位清楚；
- 当前 budget 其实只够做轻量 smoke，而不够跑重型搜索。

这时更适合先做“方法族选择”，而不是直接指定某个专用骨架。

## 决策状态与伪代码

第一阶段 Main 只维护下面的选择状态，不维护任何具体算法实现：

- `phase`：当前是建立基线，还是改进合法 incumbent。
- `instance_evidence`：从 `instance_diagnostics` 读取的规模、柔性、加工时间跨度、
  机器集中度和变体特征。
- `incumbent_evidence`：只有已有合法解时才存在的关键结构、负载、多样性和历史结果。
- `primary_search_pressure`：`construction`、`sequence`、`assignment`、`coupled`、
  `diversity` 或 `exact` 中的一个。
- `method_family`：根据主要压力选择的方法族，不是 Method Package ID。
- `knowledge_query`：2-6 个 Domain Pack 公共查询标签。

```text
profile = read(instance_diagnostics)
history = read(incumbent_and_round_evidence)
family = None
query = None
family_candidates = None

if no_legal_incumbent(history):
    foundation = [construction, initialization, decoder]
    pressure = classify_primary_pressure(profile, history)
    if low_flexibility(profile):
        family_candidates = [coupled_local_search, exact_hybrid, population_memetic]
        query = [critical_path, critical_block, local_search, cp_sat, memetic]
    elif high_flexibility(profile) and nonzero_duration_span(profile):
        family_candidates = [constructive_search, coupled_local_search, population_memetic]
        query = [high_flexibility, idle_gap, assignment_regret, decoder]
elif high_flexibility(profile) and not activated_exact_regret(history):
    pressure = assignment
    family = constructive_search
    query = [high_flexibility, idle_gap, assignment_regret, decoder]
elif high_flexibility(profile) and reliable_local_bottleneck(history):
    pressure = coupled
    family = coupled_local_search
    query = [high_flexibility, assignment_trust_region,
             order_preserving_redecode, critical_path]
else:
    pressure = classify_primary_pressure(profile, history)

family = family or compare_applicable_method_families(
    pressure, profile, budget, constraints, family_candidates
)
alternative = strongest_unselected_family(family, pressure)
query = query or public_query_tags_for(family, pressure, active_variant)

return method_family, pressure, measured_evidence, uncertainties,
       alternative_with_rejection_reason, query
```

并行分支不少于 2 时另加确定性的精确探测配额：`operation_count <= 60` 时不区分
柔性，至少一条分支使用 `exact_hybrid`；低柔性且 `operation_count <= 250` 时同样保留。
小实例精确求解分支先尝试完整变体 CP-SAT 并返回 best bound，其余分支继续执行 Main 选择的
启发式或元启发式方向。该规则是竞争配额，不是把所有方法统一改成 CP。

`classify_primary_pressure` 只能引用已测字段和 evaluator 历史；不能读取文件名、BKS、
选中的 seed 或具体 Method Package 目录。若证据不足，应把缺口写入 `uncertainties`，而不是
用固定阈值冒充确定结论。

## 第一阶段验证清单

- 输出包含一个主要方法族、一个主要搜索压力和 2-6 个公共 `knowledge_query` 标签。
- `measured_evidence` 引用了实际诊断字段或 Core 历史，不只写“大实例”“高柔性”等判断。
- 至少比较一个可行备选方向，并说明当前为何不选。
- 输出中没有 `method_package_id`、具体包合同、参考 solver 源码或实现资产路径。
- 变体约束先于方法偏好；存在 setup、transport、batching 等约束时，不得按标准 FJSP
  状态和解码语义直接规划。
- 第二阶段检索结果至少包含一张匹配实现卡或一个兼容 Method Package，之后才能签发
  Coding Worker 任务书。
- 高柔性路由必须包含 `high_flexibility`，并按阶段包含 `assignment_regret` 或
  `assignment_trust_region + order_preserving_redecode`；不能仅返回 `beam_search`、
  `initialization` 或 `decoder`。
- 方向有效性最终由候选合法性、语义覆盖和 Core promotion 结果证明；静态画像只用于提出
  可检验假设。

## 这张卡的使用方式

- `strategy` 选择阶段：判断应该先走构造、局部搜索、群体、精确还是混合。
- `direction` 选择阶段：判断下一轮应该继续精修当前方向，还是切换方法族。
- 第一阶段不得选择具体 `method_package_id`；只输出方法族、结构证据和
  `knowledge_query`。
- 第二阶段读取定向实现卡和候选 Method Package 后，再形成完整任务书。
- 不用于：证明某个具体 solver 已经正确，或替代 evaluator / benchmark 结果。

## 参考来源

- Chaudhry & Khan, *A research survey: review of flexible job shop scheduling
  techniques*, DOI: `10.1111/itor.12199`。
- Seiler et al., *An algorithm selection approach for the flexible job shop
  scheduling problem*, DOI: `10.1016/j.ejor.2022.01.034`。
- Watson et al., *Instance space analysis and algorithm selection for the job
  shop scheduling problem*, DOI: `10.1016/j.cor.2021.105661`。

这些来源支持按实例与算法适用区域做选择，但不提供可直接写死的通用阈值。本卡中的
方向表是可校准工程先验，最终仍以当前项目的 Core 证据为准。
