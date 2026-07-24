---
name: fjsp-solver-foundation-worker
description: 为受控 Coding Agent 实现或维护独立 FJSP 求解器的基础契约。用于解析实例、保持 CLI/JSON 输出、完整可行解码、deadline、incumbent 和诊断证据；只在 AlgoForge WorkerAssignment 明确授权时使用。
---

# FJSP 求解器基础执行器

## 触发条件

- AlgoForge `WorkerAssignment` 明确授权实现或维护独立 FJSP 求解器的基础契约。
- 当前任务需要解析实例、保持 CLI/JSON 输出、完整可行解码、deadline、incumbent 或诊断证据。

## 读取顺序

1. 先读 `WorkerAssignment`。
2. 读取其中授权的 `target_file` 与 `read_set` 中的需求、IO、实例样本、实现契约和参考骨架。
3. 需要共享状态与完整解码器模板时，再参考 [state-decoder-template.md](references/state-decoder-template.md)。

## 执行步骤

1. baseline 若缺少 `target_file` 则按任务书创建；improvement/repair 则先读取现有 `target_file`。
2. 建立稳定的 operation identity、候选机器、加工时间、job precedence、machine assignment 与 machine order 表示。
3. 所有构造与 move 都经过完整解码或等价可行性维护，不复用失效的 start time。
4. 独立维护 `current state`、`candidate state` 与 `global feasible incumbent`。
5. 使用单调时钟和绝对 deadline，并为验证、序列化和输出留出时间。
6. 仅在 assignment 允许的 `solution.json#/diagnostics` 写入有界计数。

## 权限与边界

- 本 Skill 只说明实现方法，不扩大 Main 选择的方法族，也不授权读取其他知识。
- 每道工序必须恰好一次，机器资格和加工时长必须与输入一致，precedence 与 machine non-overlap 必须同时成立。
- 不导入 Harness、evaluator、知识文件或历史解作为 solver 运行时依赖。
- 不按实例名、已知目标、固定 seed 或保存的最佳排程分支。
- 不修改 evaluator、协议、Skill、知识卡或 `WorkerAssignment`。

## 交付物

- 一个满足活动 IO、CLI 和输出 schema 的基础求解器实现或修补。
- 与其他已授权 Skill 可共享的表示、解码器、deadline 与 incumbent 接口。
- assignment 要求的编译与可选 smoke 结果，以及未实现或未激活机制的如实说明。

## 验证与停止条件

- 失败、超时或异常时必须返回合法 incumbent。
- 只有在共享表示与完整解码器闭合后，才可继续叠加构造、局部搜索或其他模块。
- 若基础不变量未满足，停止向上层方法扩展。
