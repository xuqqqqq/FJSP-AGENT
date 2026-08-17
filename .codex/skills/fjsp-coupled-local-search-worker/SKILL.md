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
2. 每次换机、交换、插入或块重排后执行完整 DAG 或等价精确解码，非法状态立即丢弃。
3. 从解码结果提取关键/近关键工序、机器紧弧和关键块。
4. 生成有界但可迭代的同机交换、插入、短块重排与替代机器重插候选。
5. 按 makespan、结构与接受规则精确评价候选，并更新 `current`、memory、关键结构与 `global best`。
6. 停滞时执行有界扰动或新入口重启，并在 deadline 前始终保留可返回的 `global best`。

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

## 验证与停止条件

- 参数必须由实例规模、可选机器分布、关键结构和实测耗时驱动。
- 若预算显著闲置，优先扩大迭代、候选覆盖或重启深度。
- 若邻域未真实激活或无法保持独立 incumbent，停止宣称局部搜索有效。
- 当 assignment 声明 `diagnostics.activation.coupled_local_search.moves_evaluated > 0` 为 required check 时，
  缺失、为零或仅输出到日志而未进入最终 diagnostics 都必须视为未激活，不得晋升。
- 在写文件前，把本次真实调用产生的计数合并到最终 `solution.json` 的
  `diagnostics.activation.coupled_local_search`。只写 stdout、注释、静态源码或未被 CLI 调用的
  telemetry 都不能通过。若第一次固定 smoke 报告 required activation 缺失，下一 checkpoint
  只修调用接线、deadline 分配和最终诊断透传，不另起一套搜索。
