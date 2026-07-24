---
name: fjsp-sdst-adapter-worker
description: 为受控 Coding Agent 把已选 FJSP 方法族适配到 sequence-dependent setup time（SDST）。仅在 runtime contract 明确激活 sequence_dependent_setup 且 Harness 授权本 Skill 时使用。
---

# FJSP SDST 适配执行器

## 触发条件

- runtime contract 已明确激活 `sequence_dependent_setup`。
- Harness 已授权本 Skill，且当前任务是在既定方法族上补入 SDST 语义，而不是重新选方法。

## 读取顺序

1. 先加载其他已获准的 Worker Skill。
2. 再读取 `read_set` 中的 setup IO、解码和 move 契约。
3. 需要 setup-aware 解码或 move 重算模板时，再参考 `knowledge/references/sdst/setup_aware_decoder_implementation_template.md`。

## 执行步骤

1. 把 setup 语义接入共享状态、时间传播和候选评价。
2. 先从 IO 契约判定 anticipatory、non-anticipatory 等时序语义，再选择、改写或等价实现模板。
3. 区分 job ready、machine ready、setup start、processing start 与 finish。
4. 在任意机器顺序、插入、交换、换机或交叉变异后，重算受影响的 setup。
5. 让 critical path、block、slack 和 move scoring 都基于 setup-aware 时间图。

## 权限与边界

- 本 Skill 是横切适配层，不自行选择构造、局部搜索、精确或群体方法。
- `incumbent`、deadline、输出和合法性不变量仍由基础 Skill 统一维护。
- 缺少 setup contract 或样本时必须报告阻断，不猜测矩阵索引或首工序语义。

## 交付物

- 一次对现有方法族的 SDST 适配实现或修补。
- assignment 允许时的激活证据：setup lookup、setup contribution、setup-aware move 与完整重解码计数。

## 验证与停止条件

- 只有在 setup 语义对时间传播、候选评价和合法性检查都闭合时，才可声称适配完成。
- 若仍沿用标准 FJSP 的旧 delta 或关键结构未 setup-aware，停止继续扩写质量主张。
