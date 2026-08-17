---
name: fjsp-constructive-search-worker
description: 为受控编码代理实现 FJSP 多规则构造、空闲间隙插入、有限前瞻、束搜索、多起点和结构去重。用于 Main 已选择 `constructive_search` 方法族时，要求根据获准知识卡和实例规模自主设计可执行搜索，而不是只写极小占位参数。
---

# FJSP 构造搜索执行器

## 触发条件

- Main 已选择 `constructive_search` 方法族。
- `WorkerAssignment` 授权了 `construction`、`constructive_search`、`beam_search`、`idle_gap`、`initialization`、`multi_start`、`load_balance` 或 `critical_dispatch` 相关知识。

## 读取顺序

1. 先读 `WorkerAssignment`。
2. 读取 assignment 中获准的相关知识卡，并映射到当前 solver 的真实数据结构与预算。
3. 需要设计 Beam、最早间隙或多规则入口时，再参考 `knowledge/references/standard_fjsp/idle_critical_beam_implementation_template.md`。

## 执行步骤

1. 设计同时决定 ready operation、机器与插入位置的构造过程，高柔性实例不能只固定最短机器。
2. 在机器时间轴上实现最早可行空闲间隙，必要时比较尾插与 gap insertion。
3. 为多规则入口建立互补排序压力，并用 assignment/order 指纹去重。
4. 让 Beam 状态保留足够继续合法构造的信息，避免 profile key 丢失关键分配差异。
5. 根据实例规模、候选机器分布、deadline 和实测层耗时确定 width、branch 和入口数。
6. 全程独立维护最佳完整可行解，不让部分状态、估计分或未解码路径覆盖 incumbent。
7. 若 Assignment 把 `candidates_evaluated > 1` 设为 required，CLI 必须先为构造方法保留足以形成至少两个完整候选的预算，再运行 exact 或其他 fallback。单棵深 DFS、单个 Beam 分支或第一次完整解耗尽全部构造预算时，应改用可中断的多状态 frontier、浅层分支后确定性补全或多个有独立上限的起点；不能只延长同一搜索调用。
8. 若约束紧导致 operation-level frontier 在预留时间内没有形成两个完整叶，使用 Assignment 授权的保守完整 seed pool 作构造兜底：以不同 job-block 顺序和合法机器选择形成完整排程，整体平移 block 消解机器冲突，并用同一完整变体 evaluator 验收。该 seed pool 只补齐搜索可达性，不替代主构造优化器。

## 权限与边界

- 不照抄模板的固定结构、评分或宽度。
- 不把占位宽度或名义多起点当作“已实现搜索”。
- 不因“没有 incumbent”或“柔性低”自动选择本方法族。没有 incumbent 只要求 foundation
  先产出合法 warm start；低柔性通常意味着机器分配近似固定、机器顺序压力更强，应优先让
  coupled local search、真实 CP-SAT/CP-LNS 或 sequence-oriented memetic 进入正式竞争。
- 合法 baseline 已存在且构造候选连续无提升时，本方法族不得仅凭默认优先级继续继承；除非
  有实测证据表明 ready-list、gap、Beam 覆盖或入口多样性仍是主要缺口。
- 若同时授权局部搜索技能，只输出结构不同的可行入口池，不重复实现后续局部改进。

## 交付物

- 一个可执行的构造搜索实现或修补。
- 一组结构不同的可行入口，供后续模块消费。
- assignment 允许时的激活证据：各入口 makespan、每层 expanded/retained/pruned、Beam winner、profile collision、规则耗时和机器 shortlist 分布。
- 必须在最终结果的 `diagnostics.activation.constructive_search` 下报告实际执行计数；
  `candidates_evaluated` 只统计已经形成完整合法 schedule 并完成目标评价的不同候选，不能用
  ready-operation 分支数、部分 Beam state 数、配置的 multi-start 次数代替。
- exact/CP-SAT 可以保留为合法 incumbent 或最终回退，但不得在构造方法形成 required 数量的完整候选前吞掉其预算；最终输出选择 exact 解时，仍须合并同次 CLI 中真实执行的构造计数。
- seed pool 的配置数、尝试数、部分 block 和重复指纹都不能计入 `candidates_evaluated`；只有完整、合法、目标已计算且结构不同的 schedule 可以计数。

## 验证与停止条件

- 只有在真实执行路径中观察到构造、Beam 或多入口机制激活时，才可声称该方法有效。
- 若预算明显闲置，优先扩大有效状态覆盖；若无法形成完整合法解，停止宣称构造搜索已闭合。
- 当 assignment 声明 `diagnostics.activation.constructive_search.candidates_evaluated > 1` 为 required
  check 时，缺失、只构造一个 baseline 或多个入口最终结构相同都不得作为有效构造搜索晋升。
- 若首次 Core 观察到 `attempts > 0` 但 `candidates_evaluated <= 1`，下一 checkpoint 必须修复搜索粒度、每起点上限和 CLI 预算分配；只补 diagnostics、重复相同深 DFS 或把 exact 结果计作构造候选均不算修复。
