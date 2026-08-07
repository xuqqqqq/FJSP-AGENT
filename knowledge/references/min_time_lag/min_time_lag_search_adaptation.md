---
id: min-time-lag-search-adaptation
type: reference
title: Min Time-Lag FJSP 构造、邻域与搜索适配
tags: [fjsp, minimum_time_lag, time_lag, lag_aware_search, critical_path, vns]
status: active
---

# Min Time-Lag FJSP 构造、邻域与搜索适配

## 研究结论

固定相邻 minimum lag 不需要发明新的 FJSP 解表示，但会改变所有时间相关决策。可靠路线是：

```text
lag-aware construction
-> lag-aware graph decode
-> critical-path / assignment neighborhoods
-> bounded VND, VNS, ILS or tabu control
-> full decode before acceptance
```

论文提供的是机制证据，不是可直接复制的默认参数。Worker 应根据实例画像、现有 incumbent 和 Assignment 预算确定候选规模。

## 构造阶段

### Ready 时间先正确

只允许 job 的下一道工序进入可选集合，并使用：

```text
job_ready = predecessor_end + fixed_min_lag
earliest_start(op,m) = max(job_ready, earliest_machine_gap)
```

lag 较大的前驱可能值得更早启动，以便其等待与其他 job 的加工重叠。可将下列信息加入派工或 RCL 评分：

- 当前候选的 earliest completion；
- 当前工序之后的 lag-augmented remaining chain；
- 候选机器负载和可用空隙；
- 启动该工序后可释放的长 lag；
- assignment regret：次优机器与最优机器对下游释放时间的差值。

不要只按 lag 从大到小排序。长 lag、机器瓶颈和剩余加工链必须联合考虑。

### GRASP / 多起点

Aallaoui 等的 GRASP×VNS 工作使用 operation-machine 候选的 restricted candidate list，并从低 makespan 增量候选中随机选择。对当前变体可保留：

- lag-aware 增量或下界形成 RCL；
- 多起点保留不同 assignment/机器序列盆地；
- 每个起点独立保存合法 incumbent。

不能照搬其矿山专属跨 job precedence、机器移动时间或 generic machine-dependent lag。

## Lag-Aware 关键结构

完整解码后，最长路即当前关键路径。关键路径可能包含：

- 权重 `p+L` 的 job arc；
- 权重 `p` 的 machine arc。

lag arc 本身不能通过交换直接删除，但它提示搜索应改变其两端附近的资源安排：

- 让长 lag 的前驱更早完成；
- 将 lag 后继移到更合适的机器或插入位置；
- 在 lag 窗口内填入其他 job 的加工；
- 缩短把 lag 前驱推迟的关键机器块。

每次接受 move 后重新计算关键路径、head/tail、slack 和关键块。禁止复用移动前的关键标记继续多步接受。

## 核心邻域

### 同机顺序移动

- 关键机器块的相邻交换；
- 关键或近关键工序的小窗口插入/重定位；
- 围绕 lag 前驱/后继的有界 block move。

先检查表示完整性和明显的 precedence 冲突，再用完整图解码判定。

### 替代机器移动

- 对关键工序、lag 边界工序或其机器瓶颈做换机；
- 从旧机器删除，在目标机器的有限窗口中试插入；
- 同时评估 processing time、目标机器位置和下游 `pred_end+lag` 传播。

只比较加工时间会错过“稍慢机器但更早启动 lag”的改进，也可能接受使下游释放更晚的伪改进。

### Destroy-Repair

停滞时可移除少量关键块和 lag 边界工序，再用 lag-aware earliest insertion 重建。repair 必须逐步维护 job readiness，并在结束后完整解码；不要把所有工序随机打散。

## VND / VNS / Tabu 控制

文献中的多层 VNS组合了随机序列交换、机器重分配和关键块局部改进。对当前受控 Worker，推荐更保守的闭环：

1. 从合法 incumbent 复制候选。
2. 轮换同机移动、换机插入和小规模 destroy-repair。
3. 每个候选先快速过滤，再完整 lag-aware 解码。
4. VND 使用 first/best improvement；VNS/ILS 可接受扰动起点，但全局 best 永不被覆盖。
5. Tabu 属性记录逆交换、旧机器或重插位置；aspiration 只在完整解码后严格改善全局 best 时触发。
6. 以 deadline、候选上限和停滞轮数限制搜索，不做无界 all-pairs 扫描。

对 permutation-with-repetition 编码，任意交换可能仍能由 job 出现次数恢复工序顺序；对显式 operation 序列则未必。Worker 必须按自己的表示证明 move 合法，不能照抄“随机交换两个位置”。

## 精确与混合修复

若 Assignment 选择 exact/hybrid，可使用：

- optional interval 表示 operation-machine assignment；
- 每条相邻 lag 约束 `start[v] >= end[u] + L`；
- 每台机器仅对 processing intervals 使用 `NoOverlap`；
- 固定大部分 incumbent，只释放关键块、lag 边界和少量 assignment 的 trust region；
- 用启发式 incumbent warm start，严格按 deadline 取最佳合法解。

不得把 lag 加进 interval size；这会错误占机。

## 候选估值与接受

允许用受影响节点的 head/tail、局部最长路或延迟传播做近似排序，但必须满足：

- 估值只减少完整解码次数；
- 最终 objective 来自完整排程；
- 解码失败、正权环、遗漏、重复、非法机器或 lag violation 都是候选拒绝；零权 SCC 应由解码器收缩或规范化；
- 非严格改善可按已选搜索机制保留为当前状态，但不能覆盖全局 best 或正式 incumbent。

## 运行证据

一次可信实现至少应报告：

- `parsed_min_lag_count`、`positive_min_lag_count`；
- lag-aware 构造/解码调用数；
- 同机、换机、destroy-repair 各自生成与接受数；
- positive-cycle/illegal/incomplete 拒绝数与 zero-weight SCC 数；
- 因 lag arc 或 lag 边界被选中的候选数；
- best 更新次数与最终 `min_time_lag_violations`。

这些是激活证据，不是 promotion 门槛；最终合法性和 makespan 仍由 Core evaluator 决定。

## 常见失败模式

- 解析了 lag，但 dispatch、下界和邻域仍使用标准 FJSP ready time。
- 先排 lag-blind schedule，再右移修复，导致早期 assignment/排序决策不可逆地失真。
- 将 `p+L` 当作机器占用时长。
- 局部 delta 忽略 job 后继的级联右移。
- 使用旧关键路径连续接受多个 move。
- 随机交换破坏编码的 job 顺序或产生正权依赖环。
- 只验证最终 schedule，无法证明搜索路径实际调用 lag-aware 逻辑。
- 把 maximum lag 的负弧、跨 job lag 或机器移动 lag混进当前简单合同。

## 主要调研来源

- Aallaoui、Azzamouri、Ren、Tchernev，*A GRASPxVNS approach for the flexible job shop scheduling with generic minimal time lags*：析取图、双向量编码、RCL 构造、关键块交换、换机和多层 VNS。
- Artigues、Huguet、Lopez，*Generalized disjunctive constraint propagation for solving the job shop problem with time lags*：时间约束传播、插入可行性和最长路更新。
- Boyer 等，*The generalized flexible job shop scheduling problem*：solution graph、关键路径换机/交换和环检查。
- Zhang、Zhang，*Time-Lag-Aware Deep Reinforcement Learning for Flexible Job-Shop Scheduling in PPVC Module Factories*（本地研究稿）：决策时 lag-awareness、machine-free 动态和 lag-augmented lower bound。

当前卡片只吸收与固定相邻 minimum lag 一致的机制，不继承这些工作的场景专属约束、网络结构或实验参数。
