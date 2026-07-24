---
name: algoforge-assignment
description: 使用经过校验的 WorkerAssignment 执行 AlgoForge 严格的 Main Agent 到 Coding Worker 交接，同时不向 worker 暴露完整 Context Packet。
---

# AlgoForge 交接契约

## 触发条件

- 任务需要把规划与执行严格拆分。
- 需要在不暴露完整 Context Packet 的前提下，把 Main 的决策交给 Coding Worker 执行。

## 读取顺序

1. Main 先读取当前 `PlanningPacket` 与受限证据。
2. 若 harness 为本轮开放只读分析子代理，则 Main 只把当前附件路径交给该单一子代理。
3. Worker 只读取 harness 编译后的 `WorkerAssignment`，并自行加载其中信任的 Worker Implementation Skills。

## 执行步骤

1. Main 完成诊断、方案比较与方向选择。
2. Main 产出且只产出一个顶层为 `direction_plan` 与 `worker_assignment` 的 JSON 对象。
3. `direction_plan` 必须包含诊断、备选方案、选择理由、一个主方法族、证据支持的补充方法族、可选 exact method package、保留规则、完整实现顺序、交付物、检查项、停止条件与完成规则。
4. 若是 incumbent 改进轮，还必须包含结构化 incumbent assessment 与一个可证伪的下一步 mutation。
5. Main 在检查证据、比较方案和做决定时，用简体中文输出 commentary；最终 JSON 中的 `reasoning_trace` 仅用于审计与兜底，不得伪装成实时 commentary。
6. Harness 校验并编译交接为 `WorkerAssignment`；Worker 只按该对象执行。

## 权限与边界

- `algoforge-main` 只读；仅可使用本轮 harness 开放的单一 analyst 子代理。
- Main 不必读取完整 incumbent 源码；Harness 已提供受限 AST capability audit。已审计存在的机制不得被 Main 描述为缺失。
- analyst 子代理只读，且不使用 `bash` 或 `edit`。
- `algoforge-worker` 不得替换 Main 选定的方法族；只能在代码层面研究并组合 allow-list 中的 Worker Implementation Skills。
- `task`、`question`、网络、未选 Skills、广泛仓库发现、完整 Context Packet、method catalog、experience memory 与旧尝试都对 Worker 禁止。
- repair revision 必须保留 direction id、method package 与 target file。

## 交付物

- 一个满足契约的交接 JSON。
- 一个由 Harness 编译出的 `WorkerAssignment`，精确定义 `target_file`、`read_set`、交付物、实现顺序、保留/禁止规则、最新反馈、检查项、预算、runtime contract、lineage 和可信 Skill ID。

## 验证与停止条件

- 两个通道都不得编造 `PlanningPacket` 中不存在的工具运行或测量结果。
- method package 只有在所有必需组件与耦合组都具备可达行为时才算完整。
- 保留 Core 已验证的 incumbent 行为，由固定 evaluator 决定合法性与目标改进；若交接越权或不完整，停止下发给 Worker。
