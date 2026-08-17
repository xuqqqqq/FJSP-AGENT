---
id: fjsp-pbpm-semantics-and-decoder
type: reference
title: 并行组批 FJSP 语义与联合解码
tags: [fjsp, fjsp_pbpm, batching, parallel_batch_machine, batch_capacity, job_family]
status: active
---

# 文献与算例

主要语义依据 Andy Ham（2017）*Flexible job shop scheduling problem for parallel batch processing machine with compatible job families*。论文以 Fattahi SFJS1-10、MFJS1-10 为实验主体，并令部分机器容量为 2。

本项目冻结的扩展算例沿用该主体，把 family 解释为不兼容族：只有同族工件可以进入同一批。固定 seed `20260722` 只用于生成 family 分配，不进入求解器提示。

# 联合解码

对批处理机上的候选批次 `B`：

```text
ready(B) = max(每个成员的前序完成时刻)
duration(B) = max(每个成员在该机器上的加工时长)
start(B) = max(ready(B), 前一批结束)
end(B) = start(B) + duration(B)
```

批内所有成员写相同的 `start/end`。后续工序从这个共享 `end` 继续传播。普通机器仍逐工序串行解码。

# 搜索重点

- 成批收益来自共享最长加工时间，但等待合批可能推迟关键工序，必须联合评价。
- 换机可能同时改变可组批集合、批时长和后续就绪时间，不能只修改机器编号。
- 局部搜索优先检查关键批的拆分、非满批与相邻同族批合并。
- 小实例适合批槽 CP-SAT；中大实例更适合构造、批邻域和局部精确修复。

# 禁止混淆

- p-batch 不是可抢占并行机，也不是一台机器同时运行多个独立区间。
- family 不是工件优先级。
- 批容量按成员工件数计数；未来若引入不同工件尺寸，必须另开 IO 版本。
