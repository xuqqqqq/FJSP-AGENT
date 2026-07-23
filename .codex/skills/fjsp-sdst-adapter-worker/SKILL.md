---
name: fjsp-sdst-adapter-worker
description: 为受控 Coding Agent 把已选 FJSP 方法族适配到 sequence-dependent setup time（SDST）。仅在 runtime contract 明确激活 sequence_dependent_setup 且 Harness 授权本 Skill 时使用。
---

# FJSP SDST Adapter Worker

本 Skill 是横切适配层，不自行选择构造、局部搜索、精确或群体方法。先加载其他获准 Worker Skill，再读取 `read_set` 中的 setup IO、解码和 move 契约，把 setup 语义接入共享状态与评价。

设计 setup-aware 解码或 move 重算时，可按需参考 [setup-aware-decoder-template.md](references/setup-aware-decoder-template.md)。模板分支不是默认答案；必须先从 IO 契约确定 anticipatory/non-anticipatory 等时序语义，再采用模板、改写模板或实现等价时间传播。

## 必须闭合的语义

- setup 由同机直接前驱、当前 operation/其 job 和实例定义共同决定；首工序使用明确的 dummy/start 语义。
- job ready、machine ready、setup start、processing start 和 finish 必须按 IO 契约区分。
- 任意机器顺序、插入、交换、换机或交叉变异后，重新计算受影响的前驱 setup；不能沿用标准 FJSP 的旧 delta。
- critical path、block、slack 和 move scoring 必须基于 setup-aware 时间图。
- incumbent、deadline、输出和合法性不变量仍由基础 Skill 统一维护。

在 assignment 允许时记录 setup lookup、setup contribution、setup-aware move 和完整重解码的激活计数。若缺少 setup contract 或样本，报告阻断，不猜测矩阵索引。
