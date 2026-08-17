---
id: fjsp-pbpm-search-adaptation
type: reference
title: 并行组批 FJSP 搜索适配
tags: [fjsp_pbpm, batch_merge, batch_split, batch_reinsert, cp_sat, memetic]
status: active
---

# 构造

用 `(family, candidate_machine)` 索引当前就绪工序。比较单件立即开批、同族最早就绪合批和容量填充三类候选，以解码后 makespan/关键路径压力选择，不把“满批率”当优化目标。

# 局部搜索

采用事务移动：复制机器选择、批归属和批序，执行 `merge/split/member_move/batch_reinsert/change_machine`，然后完整重解码。非法移动不进入有效评价计数；当前状态和全局最优必须分离。

# 群体与模因

个体至少包含操作机器基因和批优先键。统一解码器按工件前置产生合法批序；交叉后按 family/capacity 修复批归属，并对关键批执行有界局部改进。

# 精确混合

完整模型对每台批机提供有限批槽。成员选择决定槽是否激活、family、容量和批时长；批区间参加机器互斥。规模较大时只释放关键机器的一组相邻批、成员归属和少量机器选择，其余使用 incumbent hint/固定状态。

OR-Tools 条件约束使用 `constraint.only_enforce_if(literal)`（或 CamelCase
`OnlyEnforceIf`），不存在 `only_if` / `OnlyIf`。精确分支必须用含至少一条真实 presence 条件
的 smoke 到达 `CpSolver.solve/Solve`；仅导入、建模或编译通过不算运行。

若多个候选 interval 共享工序语义起止，候选加工时长等式必须由各自 presence 条件控制。
机器资源互斥的 `NoOverlap` 必须接收 optional/fixed `IntervalVar`，不能误传 presence
`BoolVar`；后者只负责条件激活和唯一选择。此错误只有实际执行模型构建才能发现，语法编译不能覆盖。
interval 规模应在每次创建 optional/fixed interval 时显式累计，不要调用版本相关的
`ConstraintProto.WhichOneof()` 反射；OR-Tools 9.15 的 Python 包装对象不提供该方法。
不得为所有候选无条件同时加入 `semantic_start + candidate_duration == semantic_end`；当候选时长
不同时，这会在求解前直接制造矛盾。小型 probe 必须覆盖至少一道候选时长不同的工序。

批槽激活变量表达成员 OR，而不是成员数量：对每个成员加入 `slot_present >= member_i`，再加入
`slot_present <= sum(members)`。不要写 `slot_present == sum(members)`；容量大于 1 时 BoolVar
等式会禁止多成员批。时间 horizon 必须覆盖机器拥堵后的完整排程，可采用已验证 incumbent
makespan 或全体工序候选最大时长总和；最长单个 job 时长和不是安全上界。

# 能力验收

合法单件批是必要 fallback，但不能单独证明组批机制有效。对于存在同族兼容工序、批容量大于
1 的专用小型 smoke，必须产生至少一个由固定 evaluator 验证的合法多成员批。exact lane 应在
“至少一个槽含两个或更多同族成员”的约束下优化 makespan；无约束最优解无需合批不能替代能力验证。
能力验收的接受顺序是先满足合法真实合批，再比较 makespan；不能让更小的全单件批 incumbent
覆盖已经找到的合法合批解。普通非能力验收任务仍按需求文档定义的原始目标排序。
报告必须区分 `batch_count`、`grouped_batch_count`、尝试/接受的 merge 数和真实批槽求解计数。
