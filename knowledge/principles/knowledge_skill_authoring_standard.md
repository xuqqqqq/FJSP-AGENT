# Skill 与知识库编写规范

更新时间：2026-07-24。

## 1. 一句话边界

- **Skill** 保存 Agent 完成一类任务时必须遵循的可执行工作流，回答“什么时候触发、按什么顺序做、读什么、交付什么、如何验收和停止”。
- **知识库** 保存可跨工作流复用的领域事实与证据，回答“问题或方法是什么、为什么有效、适用于什么结构、实现时有哪些选择与风险”。
- **Domain Pack** 保存问题族能力、方法族、知识标签和 Worker Skill 的注册关系，回答“当前任务可以检索和授权哪些资产”。
- **Method Package** 是知识库中的完整方法合同；只有当多个部件必须协同实现时才使用，不能替代 Skill 的执行流程。

## 2. 依据

本规范综合以下一手资料：

- [OpenAI Codex：Build skills](https://learn.chatgpt.com/docs/build-skills)
- [Agent Skills specification](https://agentskills.io/specification)
- [Agent Skills best practices](https://agentskills.io/skill-creation/best-practices)
- [OpenCode：Agent Skills](https://opencode.ai/docs/skills/)

共同规则是渐进披露：Agent 平时只看到 `name` 和 `description`；触发后读取完整 `SKILL.md`；脚本、参考资料和资产再按需读取。因此 Skill 必须短而可执行，不能充当大而全的知识仓库。

## 3. Skill 应放什么

### 必须内容

1. `SKILL.md` YAML frontmatter：
   - `name`：1-64 个字符，使用小写字母、数字和单连字符，且与目录名一致。
   - `description`：1-1024 个字符，同时说明能力与触发场景；这是主要触发机制。
2. Markdown 正文：
   - 任务目标和输入前提。
   - 必须读取的输入及读取顺序。
   - 可执行步骤和关键决策点。
   - 权限、禁止事项和失败处理。
   - 交付物、验证标准和停止条件。

正文使用祈使句，优先写模型不知道的项目约束。不要重复一般编程常识，也不要在正文另写一份与 `description` 重复的“何时使用”。

### 可选目录

| 目录 | 放置内容 | 使用条件 |
| --- | --- | --- |
| `scripts/` | 可重复、确定性、容易写错的可执行操作或校验器 | 必须实际运行测试；不要把需要判断的研究流程脚本化 |
| `references/` | 与该 Skill 紧耦合的 API、schema、工具协议和详细工作流 | 仅当别的 Skill/知识检索不会复用；通用算法知识应放项目知识库 |
| `assets/` | 输出模板、图片、字体、静态数据、待复制骨架 | 不放执行逻辑，不作为默认答案 |
| `agents/openai.yaml` | Codex UI 的显示名称、短描述与默认提示词 | 是 Codex 扩展，不属于可移植 Skill 核心 |

### Skill 不应包含

- 某次运行的 makespan、seed、日志路径、失败猜测和临时结论。
- 大段论文综述、Benchmark 数值表、通用算法原理或实例画像。
- 可被多个方法族复用的算法代码模板。
- Domain Pack 注册关系、模型选择、任意文件权限或 evaluator 结论。
- `README.md`、安装指南、变更日志等与 Agent 执行无关的附属文档。

### 推荐模板

```markdown
---
name: example-worker
description: 完成某项明确能力。用于出现某类输入、任务或已选方法族时；不用于另一类边界场景。
---

# 示例执行器

## 输入与前提

- 读取任务合同和获准资产。

## 工作流

1. 验证输入和前置条件。
2. 选择与当前证据匹配的实现方式。
3. 产出最小、可复验的变更。

## 权限与边界

- 只修改授权文件。
- 不把局部 smoke 当作正式 evaluator 证据。

## 交付物

- 代码、激活证据和风险说明。

## 验证与停止条件

- 明确通过条件、失败处理和停止条件。
```

## 4. 知识库应放什么

### 知识类型

| 类型 | 目录 | 内容 |
| --- | --- | --- |
| 原则与契约 | `knowledge/principles/` | 架构边界、质量合同、问题族建模原则 |
| Benchmark 事实 | `knowledge/benchmarks/` | IO 格式、数据集范围、LB/UB/BKS 来源和口径 |
| 稳定方法知识 | `knowledge/references/<problem_family>/` | 方法原理、适用画像、状态表示、伪代码、实现选择、失败边界和验证 |
| 完整方法包 | `knowledge/method_packages/` | 必须整体协调的方法合同、行为合同和实例无关参考实现 |
| 能力快照 | `knowledge/capabilities/` | 当前可复核能力和缺口；只保留最新有效快照 |
| 本周实验记忆 | `knowledge/experiment_memory/current_week/` | 单次运行证据和候选经验；不参与默认 RAG |
| 外部材料 | `knowledge/imported/` | 原始论文、外部代码库索引和未经提升的摘要 |

### 稳定知识卡格式

```markdown
---
title: "知识卡标题"
description: "供检索和列表展示的一句话摘要"
knowledge_type: "reference-standard"
problem_family: "standard_fjsp"
tags: ["construction", "beam_search"]
status: "reviewed"
source: "论文、官方文档、代码或复现实验路径"
created_at: "2026-07-24T00:00:00Z"
---

# 知识卡标题

## 结论摘要

## 适用问题与实例特征

## 不适用条件和反例

## 状态表示与关键不变量

## 方法或伪代码

## 实现选择，不是强制答案

## 验证方式

## 证据与来源
```

### 知识卡质量门禁

- 事实、推断和建议必须区分；建议必须能反查来源。
- 明确适用条件、反例、前置不变量和验证方式，不能只写“推荐使用某算法”。
- 参考代码是可拒绝、可改写的实现材料，不是强制照抄答案。
- 单次 promotion 只证明冻结评测下更优，不能直接证明机制具有通用因果效果。
- 本周实验经验只有经过人工审核、复现或消融后，才能去除实例分数并提升为稳定知识。

## 5. 注册与生效

- 文件存在不等于运行时可见。
- 稳定知识卡需要按标签注册到 `domain_packs/<family>/domain_pack.json` 的 `knowledge.tagged_cards`，才会进入二阶段检索。
- Worker Implementation Skill 需要注册到 `worker_implementation_skills`，并声明可覆盖的方法族和激活标签，才会进入 Worker 自动匹配。
- 前端创建界面可以执行上述注册，但默认禁止覆盖同名资产；实验记忆、原则、Benchmark 和导入材料不能直接加入自动检索。

## 6. 当前仓库审计结论

当前 Skill 的主正文已经按工作流组织。此次清查进一步完成了以下边界收敛：

1. Beam、局部搜索、CP-SAT、Memetic、setup-aware decoder、AWLS 机制和通用优化手册已迁到 `knowledge/references/`。
2. Skill 只保留读取时机、执行步骤、边界、交付物和验证条件，并链接迁移后的知识卡。
3. `solver_contract`、实验模板、状态/解码合同等与 Worker 工作流紧耦合的材料继续作为 Skill-local reference。
4. Domain Pack 标签路径和架构测试已经同步更新，不存在通过旧 Skill 路径检索已迁移算法材料的情况。
