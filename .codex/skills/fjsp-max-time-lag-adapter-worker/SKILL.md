---
name: fjsp-max-time-lag-adapter-worker
description: 为受控编码代理把已选 FJSP 方法族适配到固定、与机器无关、稀疏且可非相邻的最大时间间隔约束。仅在运行时契约明确激活 `maximum_time_lag` 且 Harness 授权本技能时使用。
---

# FJSP 最大时间间隔适配执行器

## 触发条件

- 运行时契约已明确激活 `maximum_time_lag`。
- Harness 已授权本技能，且任务是在既定方法族上补入最大时间间隔语义，而不是重新选择方法族。
- 活动 IO contract 的约束是固定四元组 `(job_id, from_op, to_op, L_max)`，其中 `from_op < to_op`，语义为 `start(to_op) <= end(from_op) + L_max`。
- 约束集允许稀疏记录，且同一 job 内允许非相邻工序对。

若任务同时含最小时间间隔、无等待、SDST、运输时间、有限等待缓冲或跨工件时间间隔，本技能不足以单独覆盖，必须先取得对应变体合同。

## 读取顺序

1. 先加载 `fjsp-solver-foundation-worker` 和当前方法族技能。
2. 读取 Assignment `read_set` 中的需求与 IO 文档。
3. 读取 `knowledge/references/max_time_lag/max_time_lag_semantics_and_decoder.md`。
4. 需要构造、邻域、修复或精确建模时，再读取 `knowledge/references/max_time_lag/max_time_lag_search_adaptation.md`。

## 执行步骤

1. **确认语义**：maximum lag 是 job 内部的 machine-free finish-start 上界，满足 `start(v) - end(u) <= L_max`。它限制后继最晚何时开始，不占用前驱机器，也不等价于后继的独立 due date。
2. **接入状态**：解析稀疏 pair 列表，建立按前驱、后继和 job 的索引；`L_max >= 0`，未列出的工序对只保留标准 precedence。非相邻 pair 必须按原记录保留，不能擅自只投影到相邻链。
   - 机器候选必须保留为结构化 `(machine_id, processing_time)` 对；抽样候选后先显式解包，再分别用于机器字典键和加工时长。禁止把整个二元组当 `machine_id`，也禁止把 `machine_id` 当加工时长。
3. **完整可行性传播**：任何构造、换机、交换、插入、变异或修复后，都必须由同一个 max-lag-aware 完整解码器重新判定。仅用前向 earliest 或最终右移修补都不够；必须处理 precedence lower bounds 与 max-lag upper bounds 的联合可行性。
4. **适配已选方法族**：
   - 构造搜索使用 earliest/latest 可行窗口与 pair slack，筛掉明显违反 max-lag 的候选；
   - 耦合局部搜索围绕 tight/violated max-lag pair、其桥接链和相关机器块生成 move，最终接受必须完整重解码；
   - 精确混合必须加入真实 CP-SAT 约束 `start[to] <= end[from] + L_max`，并记录 posted constraint 数与 solver status 作为激活证据；
   - 群体/模因搜索中的每个个体都必须经过同一 max-lag-aware 完整解码和合法性检查。
5. **保留 incumbent**：解码失败、不完整、差分约束不一致、机器重叠、max-lag 违反或目标退化的候选不能覆盖当前合法 best。若存在零时长导致的零权环，只能在不破坏上界语义的前提下规范化处理，不能把正约束环误判为可行。
6. **提交证据**：报告解析 pair 数、非相邻 pair 数、完整解码次数、因 max-lag 不一致或非法而拒绝的候选数、tight pair 命中数、max-lag-aware move 数，以及 Core 的 `max_time_lag_violations`。若走 exact/hybrid，还要报告 `cp_sat_max_lag_constraints_posted` 与非空 solver status。

### 规范激活遥测

这些字段名是 Harness 的机器验收合同，必须按所选方法族写入 solution `diagnostics`，不能改名、移层或用源码存在性代替：

- 构造：`diagnostics.activation.constructive_search.candidates_evaluated > 1` 且 `diagnostics.activation.maximum_time_lag.constructive_candidates_evaluated > 1`；后者只统计完成完整 max-lag-aware 解码的候选。
- 耦合局部搜索：`diagnostics.activation.coupled_local_search.moves_evaluated > 0` 且 `diagnostics.activation.maximum_time_lag.moves_evaluated > 0`；后者只统计受 max-lag pair/slack 驱动并完整重解码的 move。
- 精确混合：`diagnostics.cp_sat_called == true` 且 `diagnostics.solver_evidence.max_lag_constraints_posted > 0`；posted 数必须来自本次实际提交的 `start[to] <= end[from] + L_max` 约束。
- 群体/模因：除通用 population counters 外，还要有 `diagnostics.activation.maximum_time_lag.individuals_decoded > 0`，只统计完整变体解码后的个体。

导入 OR-Tools、定义但未调用 CP helper、仅在最终输出后检查 max-lag、生成未评估候选，均不得增加上述计数。

对 max-lag constructive lane，required 的两个构造计数优先于 exact fallback 的剩余时间。真实 CLI
必须使用可中断的多状态/多起点控制，让至少两个结构不同的完整 schedule 通过同一个差分约束解码器；
不得给单棵深 DFS 一个共享 deadline 后因其返回 `None` 就直接把全部时间交给 CP-SAT。若首次运行出现
`attempts > 0` 但两个计数仍为 0 或 1，repair 必须改搜索粒度、每起点节点/时间上限及预算分配，不能只补字段。
当 operation-level 主搜索在预留预算内仍没有完整叶时，必须在 exact 前启用保守完整 seed pool：把 job
链作为连续零等待 block，变化 job-block 顺序和合法机器选择，先拒绝桥接加工长度本身超过 `L_max`
的分配，再整体平移 block 消解机器冲突。每个 seed 仍须通过同一个完整 max-lag evaluator；只统计
指纹不同的合法完整 schedule，不能把 exact 解、重复 seed、尝试次数或部分状态计入构造证据。

任何正式方法 lane 都必须从真实 CLI 入口执行获准方法，并把同一次运行产生的 canonical counters
写入最终 solution diagnostics。父 CP-SAT incumbent 可以作为 fail-safe，但不能吞掉 coupled、constructive
或 population probe；若第一次 smoke 只看到父 exact 诊断，下一 checkpoint 应优先修复 CLI 接线、
deadline 分配和 diagnostics 合并。

Baseline 的职责只是在搜索前建立一个合法锚点。先完成 parser/CLI、完整变体校验和一个确定性合法 incumbent，再由正式 lane 增加窗口 Beam、regret、局部搜索或精确搜索。任何 fallback 若未通过同一个完整校验器，必须以非零状态退出且不得写出 solution；禁止为了“保持输出完整”而序列化已知非法排程。

若有界构造基线已连续失败且仍不存在 Core-valid incumbent，Harness 可把最后一次基线机会重绑到同一变体的 `exact_hybrid` 方法包，执行只求可行解的 CP-SAT 救援。该切换必须使用新的模型命令会话、继承当前物化 solver 文件、真实发布所有 max-lag 约束，并继续受相同 Core 合法性门控制；不得注入手写排程或已知答案。
exact rescue 不仅要定义模型函数，还必须把它接入真实 CLI 求解路径，并在旧 constructive 失败退出前
实际调用。若首次 rescue 的 Core 证据显示 `cp_sat_called != true`，应保留已生成代码但使用新的
command session 做一次同包接线修复；源码中出现 `cp_model` 或不可达 helper 不能算激活。

## 实现底线

- 不允许先生成 max-lag-blind 排程，再只靠右移做最终修补；maximum lag 的修复常常需要前后双向传播、提前后继或延后前驱相关链。
- 不允许把 maximum lag 折算进加工时长或机器占用区间。
- 不允许把 max-lag 简化成后继单点 due date 而忽略前驱完工时刻变化。
- 不允许复用标准 FJSP 的旧 delta 后直接接受 move；局部估值只能用于筛选。
- 不允许在 exact/hybrid 中省略真实上界约束，或只做 hint/日志而没有实际 `start(to) <= end(from) + L` 建模。
- 不允许把 minimum lag、跨工件 generic lag 或 machine-dependent transfer lag 偷渡进当前合同。

## 验证与停止条件

至少覆盖以下微型行为：

- `L_max` 边界等号合法，超出 1 个时间单位非法；
- 非相邻 pair 在中间工序、换机或交换后仍被正确检查；
- 前驱机器在 `end(from)` 后立即释放，max-lag 等待不占机；
- 一次同机交换和一次换机重插后，完整重解码仍满足所有 max-lag；
- 需要提前后继或联动桥接链的反例不能靠单纯右移修补；
- 差分约束不一致的候选被拒绝且 incumbent 保留；
- exact/hybrid 路径能证明真实 CP-SAT max-lag 约束已被 posted，而不是只输出名义标志；
- 输出恰好包含每道工序一次，Core 返回 `valid=true` 且 `max_time_lag_violations=0`。

只证明“最终输出合法”不足以声称适配完成。若构造决策、关键结构、局部接受或精确模型仍是 max-lag-blind，停止质量主张并继续修补当前阶段。
