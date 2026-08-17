---
name: fjsp-alternative-path-adapter-worker
description: 为受控编码代理把已选 FJSP 方法族适配到每个作业恰选一条工艺路线的替代工艺路径约束。仅在运行时契约明确激活 `alternative_path` 且 Harness 授权本技能时使用。
---

# FJSP 替代工艺路径适配执行器

## 触发条件

- 运行时契约已明确激活 `alternative_path` 或 `route_choice`。
- Harness 已授权本技能，且任务是在既定方法族上补入替代工艺路径语义，而不是重新选择方法族。
- 活动 IO contract 的语义是：每个 job 的候选路线集合为原始路线 `route_id=0` 加上尾部给出的 `K` 条替代路线；`op_id` 始终引用原始 operation pool 中的 0-based 工序编号；每个 job 必须且只能选择一条路线。

若任务同时引入可重入、时间间隔、日历、SDST、运输或跨作业路线耦合，本技能不足以单独覆盖，必须先取得对应变体合同。

## 读取顺序

1. 先加载 `fjsp-solver-foundation-worker` 和当前方法族技能。
2. 读取 Assignment `read_set` 中的需求与 IO 文档。
3. 读取 `knowledge/references/alternative_path/alternative_path_semantics_and_validation.md`。
4. 需要构造、邻域或精确建模细节时，再读取 `knowledge/references/alternative_path/alternative_path_search_adaptation.md`。
5. 读取当前获准的方法包契约。

## 执行步骤

1. **确认语义边界**：原始 job 路线是 `route_id=0`，尾部每条路线是同一 job 的一个替代工序序列；它不是任意 route graph，也不是可多选的工序子集。
2. **接入状态**：完整解析每个 job 的 `K` 和 K 条尾部路线，保留原始路线与所有替代路线的统一索引。`selected_routes[job_id]` 必须覆盖每个 job，且 route id 只能落在 `0..K`。
3. **闭合解码**：所有构造、route switch、换机、交换、重插、repair 或 exact extraction 后，都必须由同一个 full decoder 验证“恰好调度所选路线的全部工序且仅这些工序”，并校验路线顺序 precedence、候选机器和机器不重叠。
4. **适配已选方法族**：
   - 构造搜索必须显式做 route-first 或 route-regret 决策，并保留多起点；
   - 耦合局部搜索必须存在 route-switch 机制，并在 route 切换后做事务式完整重解码，再配合同机/异机顺序邻域；
   - exact/hybrid 必须使用真实 route one-hot、optional intervals 与 conditional precedence，不得只用日志标志替代建模。
5. **保留 incumbent**：部分路线、重复工序、遗漏所选工序、未更新 `selected_routes`、route switch 后 stale schedule、非法机器或目标退化的候选都不能覆盖当前合法 best。
6. **提交证据**：报告解析的替代路线数、非原始路线使用数、route-first 或 route-switch 命中数、完整重解码次数、因 selected-route 不一致被拒绝的候选数，以及 exact lane 的非空 CP-SAT 执行状态。

### 规范激活遥测

这些字段名是 Harness 的机器验收合同，必须按所选方法族写入 solution `diagnostics`，不能改名、移层或用源码存在性代替：

- 构造：`diagnostics.activation.constructive_search.candidates_evaluated > 1` 且 `diagnostics.activation.alternative_path.route_configurations_evaluated > 1`；后者只统计 selected_routes 已确定并完成路线诱导全解码的候选。
- 耦合局部搜索：`diagnostics.activation.coupled_local_search.moves_evaluated > 0` 且 `diagnostics.activation.alternative_path.route_switch_moves_evaluated > 0`；后者只统计真实切换 route、重建 operation set 并完整重解码的 move。
- 精确混合：`diagnostics.cp_sat_called == true`，并同时满足 `diagnostics.solver_evidence.route_one_hot_constraints_posted > 0`、`route_optional_intervals_posted > 0`、`route_conditional_precedences_posted > 0`。
- 群体/模因：除通用 population counters 外，还要有 `diagnostics.activation.alternative_path.route_mutations_evaluated > 0`，只统计切换路线并完整解码后的 mutation。

固定 `route_id=0`、只输出 `selected_routes` metadata、普通非 optional intervals、无条件发布全部路线 precedence、生成未评估 route 邻居，均不得增加上述计数。

对 coupled lane，定义 `run_coupled_local_search` 之类 helper 不等于适配完成。真实 CLI 必须给该搜索
分配非零 deadline/迭代预算并实际调用，然后把同一次调用的 `moves_evaluated` 与
`route_switch_moves_evaluated` 写入最终 solution diagnostics。可以保留 exact incumbent 作为回退，
但不能因其目标更好而跳过获准方法族的 bounded probe。

## 实现底线

- 不允许把 route choice 误写成普通 machine flexibility。
- 不允许对一个 job 同时混用多条路线的工序。
- 不允许输出原始 operation pool 的全量工序，除非该 job 选择的就是完整原始路线。
- 不允许只修改 `selected_routes` 而不重建 schedule，也不允许只修改 schedule 而不更新 `selected_routes`。
- 不允许在 exact/hybrid 中省略真实 route one-hot 或条件 precedence。
- 不允许从 Harness parser/evaluator 导入实现；求解器必须在允许路径内独立解析、选路和生成解。

## 验证与停止条件

至少覆盖以下微型行为：

- `route_id=0` 等于原始顺序；
- 非原始路线可以跳过原始 pool 中未被所选路线包含的工序；
- 未选路线工序出现在输出中会被拒绝；
- `selected_routes` 缺失、越界或与 schedule 不一致会被拒绝；
- 一次 route switch 后，完整重解码仍满足 job precedence 和 machine capacity；
- exact/hybrid 路径能证明真实 route one-hot、optional interval 和 conditional precedence 已被 posted，并有非空 CP-SAT 执行状态；
- 输出包含每个 job 的 `selected_routes`，且 Core 返回 `valid=true`。

只证明“最终 schedule 合法”不足以声称适配完成。若构造、局部搜索或精确模型仍不能显式处理 route choice，停止质量主张并继续修补当前阶段。
