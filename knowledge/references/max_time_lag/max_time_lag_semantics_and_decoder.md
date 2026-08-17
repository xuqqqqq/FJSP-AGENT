---
id: max-time-lag-semantics-and-decoder
type: reference
title: 最大时间间隔 FJSP 语义、差分约束与解码合同
tags: [fjsp, maximum_time_lag, time_lag, difference_constraints, lag_aware_decoder]
status: active
---

# 最大时间间隔 FJSP 语义、差分约束与解码合同

## 适用范围

本卡只覆盖当前 `fjsp_max_time_lag` 合同：标准 FJSP 主体后追加固定约束列表，每条约束连接同一工件的一个前驱工序和其后方某道工序，工序对集合稀疏，且允许非相邻。

对记录 `(j, a, b, L)`，其中 `a < b`：

```text
start[j,b] <= end[j,a] + L
end[j,a] = start[j,a] + processing_time[j,a,assigned_machine]
```

等价地：

```text
start[j,b] - start[j,a] <= processing_time[j,a,assigned_machine] + L
```

`L >= 0`。未列出的工序对只保留标准前驱下界，不自动生成新的 maximum lag。

以下内容不在本卡范围内：minimum lag、no-wait 等式、跨工件 lag、依赖机器选择的运输 lag、SDST、lag buffer 容量或随机 lag。

## 不占机语义

maximum lag 限制的是前驱完工到后继开始之间允许经过的最长时间，但不占用前驱机器。

- 前驱机器在 `end[j,a]` 立即释放。
- 后继若更晚开始，可能违反 `start[j,b] <= end[j,a] + L`。
- 禁止把 `processing_time + L` 作为机器占用时长；这会凭空制造机器拥塞。
- 也禁止把该约束降格成“后继固定 due date”，因为前驱完工时刻会随 assignment 和 machine order 改变。

## 输入状态

独立求解器应保存：

```text
max_lag_pairs_by_job[j] = [(from_op, to_op, L_max), ...]
outgoing_max_lag[(j, from_op)] = list of (to_op, L_max)
incoming_max_lag[(j, to_op)] = list of (from_op, L_max)
assignment[(job_id, op_id)] = machine_id
machine_sequences[machine_id] = ordered operations
```

解析尾部时必须检查：

- 在当前 IO 合同下，剩余 token 数恰好为 `1 + 4*K`；
- job/op 索引合法；
- `from_op < to_op`；
- `L_max >= 0`；
- 同一 `(job_id, from_op, to_op)` 不重复。

不得仅凭尾部形状猜测 min/max lag；应以活动 IO contract 和变体标识为准。

## 为什么最长路不再足够

固定 machine sequence 后，标准前驱约束与 machine order 只提供 lower bounds：

```text
start[v] >= start[u] + w
```

minimum lag 也仍是 lower bound，因此可用最长路求最早排程。但 maximum lag 引入的是 upper bound：

```text
start[v] <= start[u] + p(u) + L
```

这意味着单纯“前向最早时刻传播 + 最终合法性检查”不够，因为：

1. 最早排程可能违反 max-lag，而另一个通过延后前驱或重排机器得到的排程却可行；
2. 只做右移修补无法把过晚的后继提前，也无法联动桥接链；
3. 非相邻工序对会把远端工序重新耦合，局部增量更新很容易漏掉上界传播。

因此，固定 assignment 与 machine sequences 后，必须求解完整差分约束系统，而不是只算最长路。

## 差分约束解码合同

令 `x[o] = start(o)`。统一写成：

```text
x[v] - x[u] <= b
```

则可得到三类约束：

- 标准 job precedence：`start[next] >= start[cur] + p(cur)`
  - 改写为 `x[cur] - x[next] <= -p(cur)`；
- 机器顺序：若 `u` 在机器上紧邻先于 `v``
  - 改写为 `x[u] - x[v] <= -p(u)`；
- maximum lag：`start[to] <= end[from] + L`
  - 改写为 `x[to] - x[from] <= p(from) + L`。

### 一种可接受的完整解码流程

1. 为每道工序建立一个变量节点。
2. 把上述全部约束写成差分约束边。
3. 加入 super source，向所有节点连 0 边。
4. 用 Bellman-Ford、SPFA 的安全实现、或其他能检测负环的差分约束算法检查可行性。
5. 若存在负环，则 assignment/sequence 候选不可行，必须拒绝并保留父 incumbent。
6. 若可行，从势能解中提取一组 start time；必要时整体平移，使最早开始时刻非负。
7. 再次检查：每道工序一次、机器不重叠、所有 precedence 满足、所有 max-lag 满足、目标可计算。

只要提取出的结果对所有边都不违反约束，上述 start time 就不必是“最早”或“最左”的唯一排程；但它必须是真正可行的完整排程。

## 局部状态与完整重解码

构造、空隙插入、局部搜索和精确修复都可以维护局部窗口估值，但最终接受必须通过同一完整解码合同。

- 局部状态可缓存受影响子图、工序对关联关系或窗口上下界；
- 这些缓存只能减少完整解码的工作量，不能替代可行性判定；
- 返回空排程、makespan 0、只修后继不回看前驱，或只记录 violation 数但不真正提取可行时间，都不算完成。

## 非相邻工序对的传播要点

对 `(a, b, L)` 且 `b > a + 1` 的工序对，中间链上的工序和机器顺序都会影响可行性：

- 若桥接链被拖长，`b` 可能变成过晚开始；
- 若要修复 violation，可能需要提前 `b`、压缩中间链、或延后 `a` 所在机器块；
- 因此必须同时维护按前驱和按后继的稀疏索引，不能只在相邻 job arc 上打补丁。

## 空隙插入注意事项

某个机器 gap 对 maximum lag 可行，当且仅当候选开始时刻同时满足：

- 所有前驱下界；
- 机器 gap 区间；
- 所有关联 maximum lag 的最晚开始上界。

只按最早可行 gap 选位置而不看最晚窗口，可能把操作放进“对 machine 合法、对 max-lag 非法”的位置。反过来，只看最晚窗口而不看最早窗口也会破坏前驱约束。两侧窗口必须同时检查。

## 必须测试的反例

1. **边界等号**：后继在 `end(A) + L` 开始合法，再晚 1 个时间单位非法。
2. **非相邻传播**：`A -> C` 有 max-lag，而中间 `B` 换机或重插后仍要正确传播。
3. **机器释放**：A 完工后同机其他工件可立即加工；max-lag 不占机。
4. **右移失效**：某候选若只会让 `C` 更晚，必须被拒绝或触发完整重解码，而不是当作“可修复”直接接受。
5. **差分约束不一致**：lower/upper bounds 与 machine order 组合成负环时必须拒绝并保留 incumbent。
6. **零时长边界**：零时长工序与稀疏 max-lag 共存时，不得漏工序、重复工序或产生负时间提取错误。

## 证据来源与适配边界

- 广义 job shop with time lags 的差分约束/析取图文献支持把 min/max lag 一起建成差分约束，并通过环一致性判定可行性。
- generalized flexible job shop 的 solution graph 工作支持在固定 assignment 与 machine sequence 上用图结构做时间提取、关键结构分析和候选检查。

本卡只保留这些文献中对当前 machine-free、same-job、sparse maximum lag 合同可直接复用的部分：上界语义、差分约束可行性、完整重解码与非相邻工序对传播。不要把 generic cross-job lag、machine-dependent travel 或 no-wait 等式直接搬进来。
