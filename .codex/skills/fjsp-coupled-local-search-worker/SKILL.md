---
name: fjsp-coupled-local-search-worker
description: 为受控编码代理实现 FJSP 工序分配与机器顺序耦合局部搜索，包括关键路径/关键块、换机重插、交换插入、VND/ILS/Tabu 接受与重启。用于 Main 已选择 `coupled_local_search` 方法族时，要求形成可迭代闭环并保留独立 incumbent。
---

# FJSP 耦合局部搜索执行器

## 触发条件

- Main 已选择 `coupled_local_search` 方法族。
- `WorkerAssignment` 授权了 `assignment_aware_local_search`、`machine_reassignment`、`assignment_search`、`critical_path`、`critical_block`、`local_search`、`ils` 或 `tabu_search` 相关知识。
- 低柔性、候选机器稀疏或加工时间跨度小而机器顺序仍有大量组合时，本方法族应作为正式
  sequence-first 主候选；低柔性不是拒绝局部搜索的理由。

## 读取顺序

1. 先读 `WorkerAssignment` 与当前 incumbent 证据。
2. 读取获准知识卡，按当前瓶颈选择邻域。
3. 需要设计关键块、同机 move、换机重插或 Tabu/AWLS 接受时，再参考 `knowledge/references/standard_fjsp/awls_coupled_search_loop_template.md`。

## 执行步骤

1. 维护同时包含 `assignment` 与每台机器 operation order 的显式状态。
   改进任务必须先原样运行 promoted incumbent 的现有构造与目标改进路径，并把其全局最优结果作为
   新阶段 warm start；不得通过替换同名搜索函数、缩短既有预算或改写调用顺序来换取新机制接线。
2. 每次换机、交换、插入或块重排后执行完整 DAG 或等价精确解码，非法状态立即丢弃。
3. 从解码结果提取关键/近关键工序、机器紧弧和关键块。
4. 生成有界但可迭代的同机交换、插入、短块重排与替代机器重插候选。
   关键块移动停滞后，保留一个确定性的非相邻重插后备邻域：候选机器必须包含当前机器，
   对获准的有界工序/位置集合完成 full decode。可以提交有界批次中的严格最优候选，也可以接受
   首个严格改善后立即从新状态重算关键结构；不得接受一次浅改善后继续沿用旧关键块或旧顺序。
5. 按 makespan、结构与接受规则精确评价候选，并更新 `current`、memory、关键结构与 `global best`。
   对 setup-heavy SDST 的首阶段，为同机关键块重插和关键工序异机重插分别保留显式候选/时间预算；
   不得让先执行的同机扫描耗尽全部 deadline。每个有界批次优先提交 full decode 后的严格最优候选，
   接受后立即重算 assignment、机器顺序和关键结构。
   新阶段结束时必须原地更新调用者持有的 incumbent，或返回独立 best 并由真实 CLI 调用者接收；
   禁止局部 `best` 被丢弃、`best.copy_from(best)` 自拷贝，或只把改善写进 telemetry。
6. 停滞时执行有界扰动或新入口重启，并在 deadline 前始终保留可返回的 `global best`。
7. 每个候选分支都必须在分支内从当前状态独立创建 assignment/order clone；不得依赖另一个
   邻域分支曾初始化的 `cand_orders`、move 或临时序列。候选失败后丢弃该 clone。

## 权限与边界

- 不能把方法名、少量浅扫描或未激活邻域当作局部搜索已完成。
- `current state` 可暂时变差，但全局可行 `incumbent` 不得退化。
- tabu key、逆移动、aspiration、停滞与扰动必须进入实际生成、选择、应用和更新路径。
- 若同时授权构造技能，只消费其入口池并共享解码器与 incumbent。
- 不要求外部预先提供高质量 incumbent；统一 foundation 的任意合法 warm start 足以启动，
  后续质量由关键块邻域、接受、扰动和迭代闭环负责。

## 交付物

- 一个可迭代运行的耦合局部搜索闭环。
- assignment 允许时的激活证据：各邻域 generated/evaluated/accepted/improved、换机与顺序 move 分布、迭代/重启数、阶段耗时和 best trajectory。
- 必须在最终结果的 `diagnostics.activation.coupled_local_search` 下报告实际执行计数；其中
  `moves_evaluated` 必须是完成合法解码与目标评价的 move 总数，而不是生成数、循环上限或配置值。
  可同时报告 `moves_generated`、`moves_accepted`、`moves_improved`、`iterations` 与 `restarts`。
- 若 assignment 声明 setup-aware zero-slack critical-block 非相邻重插，必须另外报告：
  `tight_critical_machine_arcs`、`critical_blocks_found`、
  `nonadjacent_reinsert_moves_evaluated`、`max_reinsert_span` 与 `machine_reassign_moves_evaluated`。紧机器弧必须同时满足两端 zero-slack
  以及 `start[next] == end[prev] + setup(prev,next)`；非相邻重插必须以删除并重插后的最终索引计算
  位移，绝对位移大于 1，且只有完成 full decode 与目标评价后才增加 evaluated 计数。

## 验证与停止条件

- 参数必须由实例规模、可选机器分布、关键结构和实测耗时驱动。
- 若预算显著闲置，优先扩大迭代、候选覆盖或重启深度。
- 若邻域未真实激活或无法保持独立 incumbent，停止宣称局部搜索有效。
- 编译后必须用 assignment 允许的短 smoke 真实穿过至少一个被声明为激活的邻域分支；仅导入、
  `--help`、空循环或未触发候选生成的 smoke 不能排除活动路径上的未初始化变量和接线错误。
- 当 assignment 声明 `diagnostics.activation.coupled_local_search.moves_evaluated > 0` 为 required check 时，
  缺失、为零或仅输出到日志而未进入最终 diagnostics 都必须视为未激活，不得晋升。
- package-specific required checks 与通用 `moves_evaluated` 同时生效；一般局部移动、相邻交换、
  仅生成未解码的候选或把普通连续关键工序误报为紧关键块，都不能代替声明的具体算子 activation。
- 若 package-specific contract 要求 `machine_reassign_moves_evaluated > 0`，只有从源机器移除工序、
  插入可加工目标机器位置并完成 setup-aware full decode 与目标评价后才可计数；候选机器枚举、
  仅改变 assignment 或同机重插都不能代替该证据。
- `nonadjacent_reinsert_moves_feasible` 与 `machine_reassign_moves_feasible` 只能在 full decoder 返回
  完整合法排程后递增；在调用 decoder 前、返回 `None` 时或仅生成候选时递增均是假阳性。
- `output_incumbent_consumed` 只能由真实 CLI 路径在接收新阶段返回值或确认传入 incumbent 已被原地
  更新后写入最终 diagnostics；局部变量赋值、stdout 或未被调用者消费的返回值不能通过。
- 在写文件前，把本次真实调用产生的计数合并到最终 `solution.json` 的
  `diagnostics.activation.coupled_local_search`。只写 stdout、注释、静态源码或未被 CLI 调用的
  telemetry 都不能通过。若第一次固定 smoke 报告 required activation 缺失，下一 checkpoint
  只修调用接线、deadline 分配和最终诊断透传，不另起一套搜索。
