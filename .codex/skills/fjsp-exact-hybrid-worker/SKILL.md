---
name: fjsp-exact-hybrid-worker
description: 为受控编码代理实现 FJSP 的 CP-SAT、局部精确修复、分配信任域和启发式-精确混合搜索。用于 Main 已选择 `exact_hybrid` 方法族且运行环境支持相应求解能力时。
---

# FJSP 精确混合执行器

## 触发条件

- Main 已选择 `exact_hybrid` 方法族。
- 运行环境支持相应精确求解能力，或 `WorkerAssignment` 允许实现无依赖的有界精确子问题。
- 低柔性会减少机器选择分支，但不会消除机器排序变量；在规模和预算允许时，可提高完整
  CP-SAT 或释放关键机器顺序弧的 CP-LNS 优先级，不应只做 assignment trust region。
- 对不超过约 60 道工序的小实例，优先尝试完整 active-variant CP-SAT 模型；不要在完整
  模型可瞬时求解时只做局部 trust region。
- 对不超过约 250 道工序的低柔性实例，可先执行有界完整模型 probe；若无法闭合，再退到
  incumbent hint、局部精确修复或 assignment trust region。
- 对不超过约 250 道工序、无 sequence-dependent setup，且 optional interval 与固定 downtime
  interval 总量有界的高柔性实例，也应先运行短时完整模型 probe。高柔性本身不是排除 CP-SAT
  的理由，粗粒度 `scale` 不能代替实际变量、约束和 interval 数量估算。

## 读取顺序

1. 先读 `WorkerAssignment` 中获准的 `cp_sat`、`exact_method`、`trust_region` 和 `hybrid` 知识卡。
2. 再检查目标文件现有依赖与运行时契约。
3. 若环境允许 CP-SAT 且需要精确建模，再参考 `knowledge/references/standard_fjsp/cp_sat_trust_region_implementation_template.md`。

## 执行步骤

1. 确认是否可使用现有求解库；不得新增未授权依赖。
2. 按 assignment 实现完整模型、局部精确修复或等价的精确子问题，并闭合机器唯一选择、precedence、资源互斥和求解状态处理。
3. 显式定义局部修复时被释放的变量，其余 assignment/order 由 incumbent 或可靠 hint 约束。
4. 采用分层预算，区分短 probe 与主要连续求解预算，并使用绝对 deadline。
   时间变量上界必须是完整排程的安全上界，例如已验证 incumbent makespan 或全体工序候选
   最大加工时长之和；“最长单个 job 的加工时长和”通常只是资源冲突前的下界，不得用作全局
   horizon，否则机器拥堵会把本来可行的模型误截成 `INFEASIBLE`。
5. 明确区分 `feasible`、`optimal`、`unknown` 和 `timeout`；`unknown` 不得覆盖 incumbent。
6. 若同时授权构造或局部搜索技能，只消费它们给出的合法入口，不重复实现入口生成。
7. 报告 objective、best bound、gap、模型变量/约束/interval 规模和实际求解耗时；没有真实
   solver 调用不得宣称 exact。
   写入 `diagnostics` 或最终 JSON 前，必须把 OR-Tools 对象转换成 JSON 原生类型；solver status
   使用 `solver.status_name(status)` 或等价字符串，计数转为 `int`，目标与时间转为有限 `float`。
   禁止把 `CpSolverStatus`、IntVar、IntervalVar、numpy 标量或其他绑定对象直接放入 payload。
8. OR-Tools 解提取统一使用 `solver.Value(variable)`；新增或修改 helper 参数后，必须同步检查
   所有调用点。最后一次 exact 相关编辑后必须保留并执行一次 smoke，不得以仅通过
   `py_compile` 作为完成证据。
   新增 exact helper 后必须从实际 CLI/`main` 求解入口接线并运行；只定义 `solve_cp_sat`、
   `solve_exact` 等函数而入口仍先执行失败的旧构造路径，等同于未实现 exact。若 exact 是
   feasibility rescue，入口必须在旧构造失败退出前调用 exact，smoke 的最终输出必须携带该次
   调用产生的 `cp_sat_called`、status 和模型规模证据。
9. OR-Tools Python API 以当前环境的 snake_case 名称为准。固定 downtime interval 使用
   `model.new_fixed_size_interval_var(start, size, name)`；不要臆造
   `NewFixedIntervalVar`。模型规模使用 `len(model.Proto().variables)` 与
   `len(model.Proto().constraints)`，不要臆造 `NumVariables()` / `NumConstraints()`。
   若 assignment 开放了固定 capability probe，先验证相应方法存在。
   区间构造必须先区分是否需要显式结束变量，并优先按当前 9.15 签名的位置顺序调用，避免
   Python 绑定层的关键字名称随版本漂移：需要显式结束变量时使用
   `model.new_interval_var(start, duration, end, name)`，可选候选使用
   `model.new_optional_interval_var(start, duration, end, present, name)`。强制 interval 可显式加入
   `model.add(start + duration == end)`；可选候选若额外发布该等式，必须使用
   `.only_enforce_if(present)` / `.OnlyEnforceIf(present)`，不能无条件发布。固定长度且不需要独立结束变量时才使用
   `model.new_fixed_size_interval_var(start, duration, name)`，可选固定长度对应
   `model.new_optional_fixed_size_interval_var(start, duration, present, name)`；固定长度构造器没有
   `end` 参数，严禁传入 `end=`。这里的 `start`、`duration`、`end` 是语义角色，不要求 solver
   内部变量采用这些名字。
   每道工序应有唯一的语义起止变量，所有候选 optional interval 共享该工序的语义起止变量，
   或用 presence 条件约束把候选起止变量精确通道到语义起止变量。job precedence 只能连接相邻
   工序的语义结束与语义开始；不得对所有候选 start/end 求和来代替选择后的时间，也不得让前驱
   状态跨 job 延续。makespan 同样绑定每个 job 最后一道工序的语义结束，而不是未选候选变量之和。
   建模采用至少两阶段：第一阶段为全部 job/operation 创建语义变量、候选 presence 和 interval；
   第二阶段才添加引用前后工序的 precedence、机器 NoOverlap、makespan 与 objective。不得在下一工序
   变量尚未创建时添加前向 precedence；用容器中的 operation key，而不是假设连续索引已存在。
   smoke 必须对每个被选择候选验证“结束值减起始值等于该候选加工时长”，并验证模型目标与
   提取 schedule 的重算目标一致，防止 `size`/`end` 位置互换后错误模型仍报告 optimal。
   当同一工序存在两个加工时长不同的候选时，pre-solve smoke 必须确认模型仍可行；严禁对共享
   语义 `start/end` 同时无条件加入多个 `start + candidate_duration == end`，否则未选候选也会
   约束工序时长并把合法模型误判为 `INFEASIBLE`。
   条件约束只能使用当前 OR-Tools 的 `constraint.only_enforce_if(literal)` 或兼容的
   `constraint.OnlyEnforceIf(literal)`；不存在 `only_if` / `OnlyIf`。若封装兼容调用，允许的
   方法名列表也只能包含 `only_enforce_if` 与 `OnlyEnforceIf`。最后一次 exact 编辑后的 smoke
   必须至少实际创建一条条件约束并到达 `CpSolver.solve/Solve`，避免建模阶段 API 拼写错误被
   `py_compile` 掩盖。
10. 完整模型和较大精确邻域在 CPU 允许时使用有界并行搜索，通常设置
    `num_search_workers` 为 `2..8`，并报告实际值。不得仅为复现性强制
    `num_search_workers=1`；多线程返回的 incumbent 可正常通过 `solver.Value(...)` 提取，
    正确性由独立 evaluator 复核。仅在资源争用、单核环境或 assignment 明确要求时使用单线程。
11. 只在所有精确阶段、fallback 和 incumbent 接受决策结束后构造最终输出 payload。若接受
    exact schedule，必须原子更新 `schedule` 及其全部目标值，再写 JSON；禁止先创建包含旧
    incumbent 的 `result`，随后只替换局部变量。`solver_evidence.accepted=true` 时，其 objective
    与变种次目标必须和最终序列化 schedule 经固定 evaluator 重算的指标一致。
    激活证据必须写入顶层 `diagnostics.solver_evidence`；不要创建 solver 自声明的顶层
    `best_metrics`，该名称保留给固定 Core evaluator 汇总。
12. 批槽 membership 的创建域和消费域必须一致。若 `member[(machine, slot, operation)]` 只为
    operation 对该批机 eligible 时创建，则容量、family、成员互斥、槽激活和解提取都必须迭代
    已创建 key 或同一个 eligible-operation 列表；不得在后续约束中按全部工序笛卡尔积直接索引，
    否则模型会在 `Solve` 前因缺失 key 崩溃。最后 smoke 必须覆盖至少一条不 eligible 于该批机的工序。
13. 机器 `NoOverlap` 的参数必须是 `IntervalVar` 列表。创建普通候选 optional interval 时必须
    保存并追加 `new_optional_interval_var(...)` 的返回值；presence `BoolVar` 只用于条件激活和唯一
    选择，不能追加到 `NoOverlap`。最后 smoke 必须真实执行模型构建到 `Solve`，从而覆盖 Python
    绑定层对 interval 参数类型的检查；`py_compile` 无法发现此类错误。
14. interval 规模证据应在调用 interval 构造器时显式计数。OR-Tools 9.15 的
    `cp_model_helper.ConstraintProto` 不保证提供 protobuf `WhichOneof()`；不得为了统计 interval
    在 `Solve` 前调用 `constraint.WhichOneof("constraint")`。变量和约束总数可继续使用
    `len(model.Proto().variables)` / `len(model.Proto().constraints)`，interval 数量使用建模时累计值。

## 权限与边界

- 完整模型必须表达 makespan 与全部硬约束，局部模型必须清楚限定修复区域。
- hint、界和入口只用于搜索，不得注入实例身份、已知答案或历史排程。
- 缺少求解库时，只能报告阻断或转为 assignment 允许的无依赖有界精确子问题。
- CP-SAT 不以已有 incumbent 为前置条件；incumbent/hint 可以加速，但不得以“无 incumbent”
  为由在方法选择阶段排除 exact。正式 `exact_hybrid` 候选必须报告真实 solver 调用证据。
- `cp_sat_called=true` 只能表示代码已到达 `CpSolver.solve/Solve` 调用；仅导入 OR-Tools、创建
  `CpModel` 或在建模阶段异常都必须报告 `cp_sat_called=false`。同时报告非零模型变量、约束、
  interval 数量和 `optimal/feasible/unknown/infeasible` 状态；`runtime_error` 不算 exact 已执行。
- 模型规模优先使用规范结构
  `model_size={"variables": ..., "constraints": ..., "intervals": ...}`。兼容字段
  `interval_count` 必须表示实际创建的 interval 数量；只有 `estimated_interval_count` 而没有实际
  interval 计数时，不得声称 exact 模型已完整执行。
- 整个可选 exact probe（建模、`Solve`、状态读取和 schedule 提取）必须由异常边界保护；任何
  API、模型或提取异常都记录为 `runtime_error` 并返回 `schedule=None`，继续输出进入 probe 前
  已验证的 incumbent。exact 模块自身异常不得令独立 solver 进程退出非零。
- 只允许从 `FEASIBLE` 或 `OPTIMAL` 状态提取变量值；`UNKNOWN` 即使暴露 objective/bound
  属性也不得调用 `solver.Value(...)` 或覆盖 incumbent。

## 交付物

- 一个受当前依赖和运行时契约约束的精确或混合改进模块。
- assignment 允许时的激活证据：模型规模、释放变量数、probe/最终阶段耗时、求解状态、入口与输出 makespan。

## 验证与停止条件

- 不得把模型下界当作合法排程。
- 只有在可行 incumbent 始终可回退且求解状态被正确区分时，才可声称精确模块可用。
- 若模型不闭合、依赖未授权或 `unknown` 覆盖 incumbent，立即停止该方向。
