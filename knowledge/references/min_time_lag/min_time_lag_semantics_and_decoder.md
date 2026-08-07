---
id: min-time-lag-semantics-and-decoder
type: reference
title: Min Time-Lag FJSP 语义、时间图与解码合同
tags: [fjsp, minimum_time_lag, time_lag, lag_aware_decoder, difference_constraints]
status: active
---

# Min Time-Lag FJSP 语义、时间图与解码合同

## 适用范围

本卡只覆盖当前 `fjsp_min_time_lag` 合同：标准 FJSP 主体后追加固定约束列表，每条约束只连接同一 job 的相邻工序，且与机器选择无关。

对记录 `(j, k, k+1, L)`：

```text
start[j,k+1] >= end[j,k] + L
end[j,k] = start[j,k] + processing_time[j,k,assigned_machine]
```

`L >= 0`。未列出的相邻工序只保留标准 precedence；`L=0` 与标准 precedence 等价。

以下内容不在本卡范围内：maximum lag、no-wait 等式、跨 job lag、依赖机器选择的运输 lag、SDST、lag buffer 容量或随机 lag。

## Machine-Free 语义

lag 发生在前驱工序完工之后，约束 job 的后继释放时间，但不占用前驱机器。

- 前驱机器在 `end[j,k]` 立即释放。
- job 的下一道工序在 `end[j,k] + L` 才 ready。
- 禁止把 `processing_time + L` 作为机器占用时长；这会凭空制造机器拥塞。

这一拆分可写成两类有向弧：

- job arc `u -> v` 权重为 `p(u) + lag(u,v)`；
- machine arc `u -> v` 权重为 `p(u)`。

节点距离表示 operation start。对任一前驱弧 `(u,v,w)`，都要求 `start(v) >= start(u) + w`。

## 输入状态

独立求解器应保存：

```text
lag_after[(job_id, from_op)] = L_min
assignment[(job_id, op_id)] = machine_id
machine_sequences[machine_id] = ordered operations
```

解析尾部时必须检查：

- 剩余 token 数恰好为 `1 + 4*K`；
- job/op 索引合法；
- `to_op == from_op + 1`；
- `L_min >= 0`；
- 同一相邻工序对不重复。

不得仅凭尾部形状猜测 min/max lag；以活动 IO contract 和变体标识为准。

## 两级解码器

### 尾追加构造

仅在每台机器尾部追加操作时，可使用串行 schedule generation：

```text
job_ready[j] = predecessor_end + lag_after.get((j, k-1), 0)
start = max(job_ready[j], machine_ready[m])
end = start + processing_time[j,k,m]
machine_ready[m] = end
job_ready[j] = end + lag_after.get((j,k), 0)
```

该路径简单且必然合法，但通常不能利用机器空隙。

### Assignment + Machine Sequences 完整解码

局部搜索、空隙插入和群体变异应使用图解码：

1. 为每道 operation 建一个节点。
2. 加入全部 job arcs；受约束的相邻对使用 `p+L`，其他使用 `p`。
3. 按当前 `machine_sequences` 加入相邻 machine arcs，权重为前驱 processing time。
4. 对依赖图计算强连通分量（SCC）。由于当前所有弧权非负，SCC 内只要含一条正权边，就存在正权环，候选不可行。
5. 全部弧权为 0 的零权 SCC 是可行的：其中的零时长操作可同刻执行。将其确定性规范化或收缩为一个节点，不能仅因拓扑排序失败就拒绝。
6. 在 SCC 凝聚后的 DAG 上按拓扑序计算最长前驱距离；同一零权 SCC 内节点使用相同 earliest start。
7. 输出完整 schedule，并再次检查每道工序一次、机器不重叠和 lag 不变量。

在当前仅含非负 minimum lag 的合同下，可行性条件是“无正权环”，而不是“原图必须无环”。若所有 processing time 都严格为正，条件才退化为 DAG 检查。更一般的 min/max difference-constraint 论文也使用 positive-cycle inconsistency；不要为当前简单合同引入负弧或 maximum-lag 逻辑。

## 空隙插入注意事项

机器空隙只解决 machine availability，不能替代 job release：候选开始时刻至少为 `pred_end + L`。把操作插入已有机器序列中间后，受影响的不只是该操作；其机器后继和 job 后继可能继续右移。因此：

- 可用局部 head/tail 或增量最长路做候选过滤；
- 最终接受必须基于完整 lag-aware 解码；
- 解码失败必须返回“候选无效”，不能返回空排程或 makespan 0。

## Lag-Aware 下界

忽略机器容量、对每道未排工序取最短加工时间，可得到 job-chain 下界：

```text
estimate[j,k] = max(estimate[j,k-1] + lag[j,k-1], current_time)
                + min_processing_time[j,k]
LB = max_j estimate[j,last]
```

该下界可用于构造排序、Beam 剪枝或候选估值。它不能代替完整解码，因为它移除了机器冲突。

## 必须测试的反例

1. **机器释放**：工序 A 完成后进入长 lag，同机另一 job 必须能立即加工。
2. **边界等号**：后继在 `end(A)+L` 开始应合法，早 1 个时间单位应非法。
3. **局部移动传播**：换机或交换使前驱推迟时，job 后继链必须同步推迟。
4. **零时长/零 lag**：不得漏工序或因相同时间戳产生重复占用判断错误。
5. **正权环与零权 SCC**：含正加工时间或正 lag 的依赖环必须拒绝；只含零时长和零 lag 的 SCC 必须允许同刻执行或确定性收缩。

## 证据来源与适配边界

- Artigues、Huguet、Lopez，*Generalized disjunctive constraint propagation for solving the job shop problem with time lags*：difference constraints、最长路传播、资源析取与不一致检测。
- Boyer 等，*The generalized flexible job shop scheduling problem*：solution graph、最长路定时、关键路径邻域和候选环检查。
- Zhang、Zhang，*Time-Lag-Aware Deep Reinforcement Learning for Flexible Job-Shop Scheduling in PPVC Module Factories*（本地研究稿）：job-blocking/machine-free 语义、`p+lag` job arc、`p` machine arc，以及 lag-aware 下界。

前两项覆盖更一般的时间窗/广义 FJSP；最后一项与当前 machine-free finish-start min-lag 最接近。本文只保留三者对当前固定相邻 minimum lag 可证明成立的部分。
