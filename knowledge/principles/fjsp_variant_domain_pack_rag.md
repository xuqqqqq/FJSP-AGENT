---
id: fjsp-variant-domain-pack-rag
type: principle
title: FJSP 变体领域包与 RAG 合同
tags: [fjsp, variants, domain-pack, rag, io-contract, evaluator, skill]
status: active
---

# FJSP 变体 Domain-Pack 与 RAG 合同

## 目的

当 worker 面对标准 FJSP，或 SDST、time-lag/no-wait FJSP、machine unavailability、batching、transportation、reentrant routes、dynamic arrivals、多目标调度等变体时，应使用这张卡。

平台后端应保持 problem-family 层面的通用性。变体特定的算法、论文笔记和修复规则，应放在 Domain Pack、knowledge card、Skill、Method Package 以及有边界的 Worker Assignment 中。某次运行里失败的尝试应写入 `experiment_memory/`，而不是沉淀为稳定的方法指导。

## 变体接入顺序

1. 从任务文档、实例诊断和 evaluator 文件中识别当前生效的 IO contract。
2. 明确写出变体约束，例如 setup matrix、lag bounds、machine calendars、batch capacity、transport times、route choice、release dates、due dates 或 multiple objectives。
3. 在修改 solver 代码前，先确认合法性和目标语义由 evaluator 定义。
4. 按标签和单个 Method Package 选择知识；不要用无关论文淹没 Worker。
5. 在任何候选代码之前，都要求先给出自然语言的规则或算子假设。
6. 只根据 Core evaluator 指标和 benchmark 报告做 promotion。

## 检索规则

- 只要涉及 solver 质量判断，就必须检索 benchmark scope。只有任务明确要求某个审计日期时，才读取带日期的 capability snapshot。
- 遇到新的工业约束时，检索这张调研卡：
  `knowledge/references/general_fjsp/fjsp_scene_survey_2025_10_17.md`。
- 只检索与所选 Method Package 绑定的稳定参考资料和合同。
- 失败尝试笔记只能通过 Main 显式的 experience-memory 路径回放。默认 Worker RAG 中绝不能放入 `experiment_memory/`。
- LB/UB/BKS 只能视为报告和选门槛时的诊断信息。

## 证据卫生

- Method、Skill 和 package 指导应描述可复用机制、不变量和失败模式，而不是某个实例的目标 makespan、拷贝来的 schedule 或依赖 seed 的答案。
- 数值 makespan、LB/UB/BKS 以及逐实例 gap 应放在 benchmark、capability 或 experiment 报告中。它们是 Main 侧用于门槛和比较的诊断信息，不是 Worker solver 输入。
- 当把实验结论提升为 knowledge card 时，要把具体结果归一化成方法层经验，例如“operation-level 的 setup-aware dispatch 有帮助”或“混合表示的 local search 失败了”。
- artifact 路径只保留为审计线索。不要要求 worker 复现某个历史 artifact 的精确分数或解。

## 后端边界

后端可以加载：

- domain-pack capability metadata；
- IO/evaluator invariant 文本；
- 已选 Method Package 的 metadata 与 contract 路径；
- knowledge-card 路径和片段；
- 用于报告的 benchmark bounds。

后端不得硬编码：

- 某个具体 FJSP neighborhood 作为平台规则；
- 在通用 orchestration 中写入 SDST 特有的 setup 公式；
- 在未确认 IO contract 前写入变体特定的 parser 假设；
- 用 LB/UB 代替声明目标进行优化的 promotion 逻辑。

## 变体包检查清单

为新的 FJSP 变体新增或更新 domain pack 时，应包含：

- 支持的变体名称及别名；
- 规范目标以及可选诊断指标；
- IO contract 说明；
- evaluator invariants；
- solver 入口点或 adapter 入口点；
- 映射到 card 的知识标签；
- 可选的、带完整实现合同的 Method Package；
- 若可获得，则提供包含 LB/UB/BKS 的 smoke 与 performance benchmark 阶梯。

第一阶段里程碑应先聚焦合法性。只有在 parser、evaluator、schedule schema 和 smoke benchmark 都稳定之后，质量优化工作才应开始。
