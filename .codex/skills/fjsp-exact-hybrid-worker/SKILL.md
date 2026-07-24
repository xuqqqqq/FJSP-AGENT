---
name: fjsp-exact-hybrid-worker
description: 为受控 Coding Agent 实现 FJSP 的 CP-SAT、局部精确修复、assignment trust region 和启发式-精确混合搜索。用于 Main 已选择 exact_hybrid 方法族且运行环境支持相应求解能力时。
---

# FJSP 精确混合执行器

## 触发条件

- Main 已选择 `exact_hybrid` 方法族。
- 运行环境支持相应精确求解能力，或 `WorkerAssignment` 允许实现无依赖的有界精确子问题。

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

## 权限与边界

- 完整模型必须表达 makespan 与全部硬约束，局部模型必须清楚限定修复区域。
- hint、界和入口只用于搜索，不得注入实例身份、已知答案或历史排程。
- 缺少求解库时，只能报告阻断或转为 assignment 允许的无依赖有界精确子问题。

## 交付物

- 一个受当前依赖和 runtime contract 约束的精确或混合改进模块。
- assignment 允许时的激活证据：模型规模、释放变量数、probe/最终阶段耗时、求解状态、入口与输出 makespan。

## 验证与停止条件

- 不得把模型下界当作合法排程。
- 只有在可行 incumbent 始终可回退且求解状态被正确区分时，才可声称精确模块可用。
- 若模型不闭合、依赖未授权或 `unknown` 覆盖 incumbent，立即停止该方向。
