---
name: fjsp-exact-hybrid-worker
description: 为受控 Coding Agent 实现 FJSP 的 CP-SAT、局部精确修复、assignment trust region 和启发式-精确混合搜索。用于 Main 已选择 exact_hybrid 方法族且运行环境支持相应求解能力时。
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
2. 再检查目标文件现有依赖与 runtime contract。
3. 若环境允许 CP-SAT 且需要精确建模，再参考 `knowledge/references/standard_fjsp/cp_sat_trust_region_implementation_template.md`。

## 执行步骤

1. 确认是否可使用现有求解库；不得新增未授权依赖。
2. 按 assignment 实现完整模型、局部精确修复或等价的精确子问题，并闭合机器唯一选择、precedence、资源互斥和求解状态处理。
3. 显式定义局部修复时被释放的变量，其余 assignment/order 由 incumbent 或可靠 hint 约束。
4. 采用分层预算，区分短 probe 与主要连续求解预算，并使用绝对 deadline。
5. 明确区分 `feasible`、`optimal`、`unknown` 和 `timeout`；`unknown` 不得覆盖 incumbent。
6. 若同时授权构造或局部搜索 Skill，只消费它们给出的合法入口，不重复实现入口生成。
7. 报告 objective、best bound、gap、模型变量/约束/interval 规模和实际求解耗时；没有真实
   solver 调用不得宣称 exact。
8. OR-Tools 解提取统一使用 `solver.Value(variable)`；新增或修改 helper 参数后，必须同步检查
   所有调用点。最后一次 exact 相关编辑后必须保留并执行一次 smoke，不得以仅通过
   `py_compile` 作为完成证据。
9. OR-Tools Python API 以当前环境的 snake_case 名称为准。固定 downtime interval 使用
   `model.new_fixed_size_interval_var(start, size, name)`；不要臆造
   `NewFixedIntervalVar`。模型规模使用 `len(model.Proto().variables)` 与
   `len(model.Proto().constraints)`，不要臆造 `NumVariables()` / `NumConstraints()`。
   若 assignment 开放了固定 capability probe，先验证相应方法存在。
   创建 optional interval 时必须按当前 OR-Tools 签名的语义角色使用关键字参数：
   `start=<起始表达式>, size=<加工时长表达式>, end=<结束表达式>,
   is_present=<选择 literal>, name=<名称>`。这约束的是 API 参数角色，不约束 solver 内部变量名。
   smoke 必须对每个被选择候选验证“结束值减起始值等于该候选加工时长”，并验证模型目标与
   提取 schedule 的重算目标一致，防止 `size`/`end` 位置互换后错误模型仍报告 optimal。
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

- 一个受当前依赖和 runtime contract 约束的精确或混合改进模块。
- assignment 允许时的激活证据：模型规模、释放变量数、probe/最终阶段耗时、求解状态、入口与输出 makespan。

## 验证与停止条件

- 不得把模型下界当作合法排程。
- 只有在可行 incumbent 始终可回退且求解状态被正确区分时，才可声称精确模块可用。
- 若模型不闭合、依赖未授权或 `unknown` 覆盖 incumbent，立即停止该方向。
