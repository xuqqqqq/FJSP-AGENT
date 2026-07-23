---
name: fjsp-solver-foundation-worker
description: 为受控 Coding Agent 实现或维护独立 FJSP 求解器的基础契约。用于解析实例、保持 CLI/JSON 输出、完整可行解码、deadline、incumbent 和诊断证据；只在 AlgoForge WorkerAssignment 明确授权时使用。
---

# FJSP Solver Foundation Worker

先加载 WorkerAssignment，再读取其中明确授权的 `target_file`，以及 `read_set` 列出的需求、IO、实例样本、实现契约和参考骨架。baseline 的 `target_file` 可以尚不存在，此时应创建它而不是把缺失视为阻断；improvement/repair 必须先读取现有 `target_file`。Skill 只说明实现方法，不扩大 Main 选择的方法族，也不授权读取其他知识。

## 实现顺序

1. baseline 若不存在 `target_file`，按任务书创建；否则检查现有 `target_file`、CLI 参数、实例格式和输出 schema，并优先复用合法 incumbent。
2. 建立稳定的 operation identity、候选机器、加工时间、job precedence、machine assignment 与 machine order 表示。
3. 所有构造或 move 都必须经过完整解码或等价的可行性维护；不能复用已经失效的 start time。
4. 独立保存 current state、candidate state 和 global feasible incumbent。失败、超时或异常时返回 incumbent。
5. 使用单调时钟和绝对 deadline，给验证、序列化及输出保留时间。
6. 只在 assignment 允许的 `solution.json#/diagnostics` 写有界计数；诊断不得影响合法性或目标值。

## 不变量

- 每道工序恰好一次，机器必须可选，加工时长必须匹配输入。
- 作业内 precedence 和机器不重叠必须同时成立。
- 不导入 Harness、evaluator、知识文件或历史解作为 solver 运行时依赖。
- 不按实例名、已知目标、固定 seed 或保存的最佳排程分支。
- 不修改 evaluator、协议、Skill、知识卡或 WorkerAssignment。

## 与其他 Skill 组合

把获准的构造、局部搜索或其他实现 Skill 当作可组合模块。先确定共享表示、解码器、deadline 和 incumbent 接口，再实现各模块；不要把不同模块各自维护的半成品状态直接拼接。

实现或审查共享状态与完整解码器时，可按需参考 [state-decoder-template.md](references/state-decoder-template.md)。其中的代码只是接口样例；可以采用、改写或使用等价设计，但必须满足活动 IO、完整可行解码和 `WorkerAssignment` 的硬约束。

完成前按 assignment 指定顺序执行一次编译和可选 smoke，并如实报告未实现或未激活的机制。
