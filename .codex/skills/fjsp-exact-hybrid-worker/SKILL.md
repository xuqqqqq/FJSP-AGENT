---
name: fjsp-exact-hybrid-worker
description: 为受控 Coding Agent 实现 FJSP 的 CP-SAT、局部精确修复、assignment trust region 和启发式-精确混合搜索。用于 Main 已选择 exact_hybrid 方法族且运行环境支持相应求解能力时。
---

# FJSP Exact Hybrid Worker

读取 WorkerAssignment 中获准的 `cp_sat`、`exact_method`、`trust_region` 和 `hybrid` 知识卡，再检查目标文件现有依赖与 runtime contract。不得新增未授权依赖；缺少求解库时报告阻断或实现 assignment 允许的无依赖有界精确子问题。

环境允许 CP-SAT 且当前方案需要精确建模时，可按需参考 [cp-sat-trust-region-template.md](references/cp-sat-trust-region-template.md)。模板不是唯一实现；无论采用完整模型、局部精确修复或其他等价模型，都必须闭合机器唯一选择、precedence、资源互斥、求解状态处理和独立验证。

## 实现原则

- 完整模型必须表达每道工序恰选一台机器、job precedence、machine no-overlap 和 makespan。
- 局部精确修复要显式定义被释放变量，其余 assignment/order 受 incumbent 约束或可靠 hint 约束。
- 分层安排短 probe 与主要连续预算，使用绝对 deadline，并为可行 incumbent 返回保留时间。
- solver 状态必须区分 feasible、optimal、unknown 和 timeout；unknown 不得覆盖 incumbent。
- hint、界和入口只用于搜索，不得包含实例身份、已知答案或历史排程。

若同时获准构造或局部搜索 Skill，使用它们产生的合法且结构不同的入口；精确模块只负责定义清晰的修复区域、探测响应和最终改进，不重复实现入口生成。

在 assignment 允许时记录模型规模、释放变量数、probe/最终阶段耗时、求解状态、入口与输出 makespan；不得把模型下界当作合法排程。
