---
name: fjsp-alternative-path-adapter-worker
description: 为已授权的方法族实现替代工艺路径FJSP的路线选择、完整重解码及有界改进；仅用于alternative_path或route_choice激活的WorkerAssignment。
---

# 替代工艺路线适配

只加载Assignment列出的技能；不要另行请求foundation或其他未授权技能。
已完整内联的incumbent、IO与知识卡不重复打开；只提供路径时仍需读取获准文件。先用一句话指出本次要改的
函数和规则，然后落盘一个能从真实CLI执行的小改动；不要先重写完整求解器或补齐所有未来阶段。
`implementation_order`决定本次范围；下面只执行对应方法段落。

## 不变语义

每个job恰选一条路线：0为原始顺序，1..K为尾部替代序列。工序key始终用原始
`(job_id, op_id)`，不能把路线位置当op_id。只排所选路线工序；换路线后重建其顺序、
覆盖集合、机器时间轴并完整重解码。未选路线可以有较少或重排的工序，不能“修复”为全工序池排程。
最终`selected_routes`和`schedule`来自同一个合法best快照。

## 在现有表示上实现所选方法

- **constructive_search**：保留已有parser/decoder，先补最早空闲间隙插入或剩余工作量派工中
  当前缺失的一项；对不同路线向量与派工优先参数完整构造、比较并保存最优。
  路线静态工作量仅供初筛，最终按完整排程makespan选。不要跑两三个候选仅凑激活计数就退出；
  有剩余CLI预算时继续不同路线与优先参数的有界多起点，重复状态去重。
- **coupled_local_search**：优先复用incumbent的完整构造函数作为重解码器。
  先闭合“复制当前路线→换一个job路线→完整重解码→严格改进才更新best”的循环；
  接受后从新状态继续扫描。若当前阶段包含machine/order邻域，再复用原有优先序或分配参数
  做一个交换/重插邻域，不强制把已合法的构造式表示改写成机器DAG或Tabu系统。
  有预算且一轮无改进时可从独立扰动状态重启，全局best始终保留。
- **exact_hybrid**：用已有合法解回退，新增一个可从CLI调用的路线模型函数。
  每job-route有one-hot变量；工序presence等于包含该工序的所选route之和；
  机器presence之和等于工序presence。所有路线共享工序起止变量，precedence按route条件激活。
  先闭合真实模型、Solve和输出，再按模型规模决定是否局部释放；不要先实现复杂LNS外壳。
  只从FEASIBLE/OPTIMAL提取，UNKNOWN或异常保留旧best。详见已授权
  `knowledge/references/alternative_path/alternative_path_search_adaptation.md`的精确段。
- **population_memetic**：路线向量是染色体的一部分，变异后仍通过同一完整重解码器评分。

CLI搜索预算来自`--time-limit-sec`，短smoke时限不应固化为正式搜索上限。
先运行现有入口取得合法best；允许按Assignment在总deadline内划分旧搜索与新增阶段预算，
提前预留新增阶段时间并将返回best接回输出，不能先让旧搜索耗尽全部时限。
预算耗尽只停止搜索，不能输出未完成候选。若可枚举候选已穷尽，可以提前结束并报告原因。

## 证据与一次闭环检查

只报告实际发生的计数，写入最终JSON的`diagnostics`，不要为了计数强选劣路线：

| 方法 | 规范字段 |
|---|---|
| 构造 | `activation.constructive_search.candidates_evaluated`；`activation.alternative_path.route_configurations_evaluated`（不同路线向量完成合法解码后计数） |
| 局部 | `activation.coupled_local_search.moves_evaluated`；`activation.alternative_path.route_switch_moves_evaluated` |
| 精确 | `cp_sat_called`；`solver_evidence.route_one_hot_constraints_posted`、`route_optional_intervals_posted`、`route_conditional_precedences_posted`及求解状态 |
| 群体 | `activation.alternative_path.route_mutations_evaluated`及Assignment指定的群体计数 |

字段相对`diagnostics`；具体必需值以Assignment为准。正计数证明调用发生，不证明质量提升。
先保存代码，再原样执行获准的compile、固定smoke命令；不加`cd`、`&& echo`或另开自测命令。
在同一次获准smoke中检查selected-route覆盖、路线前驱、机器资格与不重叠、incumbent保留及所选方法计数。
若最终选择原始路线更好，就保留原始路线；路线探索能力不要求最终解采用非原始路线。
若还激活时间间隔、组批、SDST或日历，须消费Assignment中的对应约束；本技能不能替代这些语义。
