---
id: max-time-lag-search-adaptation
type: reference
title: 最大时间间隔 FJSP 构造、邻域与搜索适配
tags: [fjsp, maximum_time_lag, time_lag, lag_aware_search, difference_constraints, cp_sat]
status: active
---

# 最大时间间隔 FJSP 构造、邻域与搜索适配

## 研究结论

固定、稀疏、可非相邻的 maximum lag 不要求发明全新的 FJSP 表示，但会把所有时间相关决策从“只管最早开始”改成“必须同时尊重最早与最晚可行窗口”。可靠路线是：

```text
pair-aware construction
-> full difference-constraint decode
-> tight-pair / bridge-chain neighborhoods
-> bounded VND, ILS, tabu or exact repair
-> full redecode before acceptance
```

文献给出的是机制证据，不是默认参数。Worker 应根据实例画像、现有 incumbent 和 Assignment 预算控制候选规模。

## 构造阶段

### 不仅要看就绪集合，还要看最晚窗口

只允许工件的下一道工序进入可选集合，但 maximum lag 会给某些候选附加最晚开始上界。对候选工序 `o` 与机器 `m`，至少要同时估计：

- precedence 与 machine availability 给出的最早可行开始时刻；
- 由所有相关 max-lag 工序对导出的最晚可行开始时刻；
- gap 内是否存在 `earliest <= start <= latest` 的可行插入点；
- 消耗多少工序对 slack，以及该 slack 是否会压垮下游桥接链。

若候选只有最早时刻、没有最晚时刻，构造就仍是 max-lag-blind。

### 工序对 Slack 优先级

可将以下量加入派工或 RCL 评分：

- 当前候选的最早完工时刻；
- 相关 max-lag 工序对的剩余 slack；
- 候选对桥接链总长度的影响；
- 候选机器上的 gap 紧张度；
- assignment regret：次优机器相对最优机器在 max-lag slack 消耗上的差值。

不要只按 slack 从小到大排序。max-lag 紧张度、机器瓶颈、剩余加工链和插入位置必须联合考虑。

### 多起点 / Beam

当前变体尤其适合保留多个部分状态，因为某个局部更早的选择可能过早消耗关键 max-lag slack。可保留：

- pair-aware 增量估值或下界形成的 RCL；
- 多起点或 Beam 以探索不同 assignment/机器序列盆地；
- 每个部分状态独立保存合法 best 和完整重解码结果。

但每次状态扩展后仍要以统一的 max-lag-aware 完整解码合同验收。

当运行合同要求至少两个完整构造候选时，搜索控制必须具备 anytime 特性：为每个起点或 frontier
扩展设置独立节点/时间上限，并保留确定性补全路径，使一个困难分支不会耗尽全部构造预算。
先形成并完整解码 required 数量的结构不同候选，再把剩余时间交给 CP-SAT 或其他 fallback。
`attempts > 0` 而完整候选为 0，说明预算分配或搜索粒度失效，不是可以用 exact 输出掩盖的合法激活。

### 保守的完整初始候选池

复杂 max-lag 窗口可能让深 DFS 或窄 Beam 在节点预算耗尽前到不了完整叶。此时应保留主
operation-level window/gap 构造，同时在 exact fallback 前预留一个小而确定的完整初始候选池：

- 为每个 operation 选择合法机器；若某条 max-lag 工序对中间的连续加工长度已经大于上界，立即拒绝该分配；
- 把每个 job 链按无内部等待的连续 block 放置，使用不同 job-block 顺序与机器选择制造结构差异；
- 发生机器冲突时整体平移 job block，不在 block 内插入等待，因此不会人为破坏同 job max-lag；
- 每个完整结果仍调用统一的 max-lag evaluator，验证覆盖、加工时长、precedence、机器互斥和全部上界；
- 按机器分配、机器顺序或完整 operation 时间结构建立指纹，重复结果不增加候选计数。

这是搜索可达性的保守兜底，不是质量优化器。它只在主 frontier 未形成 required 数量的完整候选时
启用；CP-SAT 可以继续保留最佳 incumbent，但 CP 输出不能记入 constructive activation。

## 紧张工序对与桥接链

完整解码后，最值得处理的不只是传统关键路径，还包括：

- 已违反或接近上界的 max-lag 工序对；
- 从 `from_op` 到 `to_op` 的桥接 job/machine 链；
- 把 `to_op` 推晚或把 `from_op` 拉早的关键机器块。

这类结构提示三种改善方向：

- **提前后继**：把 `to_op` 或其中间桥接链更早插入；
- **压缩桥接链**：减少中间工序等待和机器绕行；
- **延后前驱**：在不显著恶化目标的前提下，延后 `from_op` 所在机器块，让上界窗口整体后移。

第三类动作正是“不能只靠右移修补”的反面例子：有时延后前驱是可行修复的一部分，但它必须通过完整差分约束重解码统一验证，而不是局部手工打补丁。

## 核心邻域

### 同机窗口重插

- 围绕 `to_op`、桥接链工序或相关关键块做小窗口前移重插；
- 对 `from_op` 所在机器块做受控后移，观察是否释放 max-lag 上界；
- 先做表示完整性和明显 precedence 检查，再做完整重解码。

### 异机换机压缩

- 对 `to_op`、桥接链瓶颈工序或 `from_op` 邻近工序做换机；
- 联合评估加工时间、目标机器 gap、桥接链压缩幅度和 slack 变化；
- 删除与插入必须作为一个可回退事务执行。

### 以工序对为锚点的 Destroy-Repair

停滞时可移除一个紧张 max-lag 工序对附近的少量工序，再用 pair-aware earliest/latest insertion 重建。repair 必须逐步维护可行窗口，并在结束后完整重解码；不要把所有工序随机打散。

## VND / ILS / Tabu 控制

推荐保守闭环：

1. 从合法 incumbent 复制候选。
2. 轮换同机窗口重插、异机压缩与 pair-anchored destroy-repair。
3. 每个候选先做快速窗口筛选，再完整 max-lag-aware 重解码。
4. ILS/tabu 可接受非严格改善的当前状态，但全局 best 永不被覆盖。
5. Tabu 属性记录逆交换、旧机器、旧插入窗口或被移动的工序对锚点；aspiration 只在完整重解码后严格改善 global best 时触发。
6. 用 deadline、候选上限和停滞轮数限制搜索，不做无界 all-pairs 扫描。

## 精确与混合修复

若 Assignment 选择 exact/hybrid，可使用：

- optional interval 表示 operation-machine assignment；
- 每条 max-lag 工序对加入真实约束 `start[to] <= end[from] + L`；
- 每台机器的 `NoOverlap` 仅覆盖 processing interval；
- 固定大部分 incumbent，只释放紧张工序对、桥接链和少量 assignment 的 trust region；
- 用启发式 incumbent warm start，严格按 deadline 取最佳合法解。

必须显式记录 posted 的 max-lag 约束数、solver status 和提取结果。只输出“启用了 max lag 模式”但没有真实上界约束，不算激活。

## 候选估值与接受

允许用工序对 slack、桥接链长度、受影响节点窗口或局部传播做近似排序，但必须满足：

- 估值只减少完整重解码次数；
- 最终 objective 来自完整排程；
- 解码失败、差分约束负环、遗漏、重复、非法机器或 max-lag violation 都是候选拒绝；
- 非严格改善可按已选搜索机制保留为当前状态，但不能覆盖全局 best 或正式 incumbent。

## 运行证据

一次可信实现至少应报告：

- `parsed_max_lag_count`、`non_adjacent_max_lag_count`；
- max-lag-aware 构造/重解码调用数；
- 紧张工序对、bridge chain、同机、换机、destroy-repair 各自生成与接受数；
- 差分约束不一致/illegal/incomplete 拒绝数；
- 工序对 slack 主导的候选数；
- best 更新次数与最终 `max_time_lag_violations`；
- 若走 exact/hybrid，再报告 `cp_sat_max_lag_constraints_posted` 与 solver status。

这些是激活证据，不是 promotion 门槛；最终合法性和 makespan 仍由 Core evaluator 决定。

## 常见失败模式

- 解析了 max-lag，但 dispatch、gap 选择和邻域仍只看 earliest ready time。
- 先排 max-lag-blind schedule，再做右移修补。
- 把 `p + L` 当作机器占用时长。
- 只把后继当 due date，不看前驱完工时刻随 move 改变。
- 邻域只移动 `to_op`，却不压缩桥接链也不允许延后 `from_op` 相关块。
- 非相邻工序对没有建立稀疏索引，导致局部增量更新漏检。
- exact/hybrid 路径没有真实 `start(to) <= end(from) + L` 约束或没有 activation evidence。
- 只验证最终 schedule，无法证明搜索路径实际调用了 max-lag-aware 逻辑。

## 主要调研边界

广义 job shop with time lags、generalized FJSP solution graph 和相关局部/精确修复工作都支持以下共识：maximum lag 是上界差分约束，候选需要完整可行性传播，紧张工序对与桥接链是比单纯关键路径更直接的搜索锚点。

当前卡片只吸收与固定、same-job、machine-free、sparse maximum lag 一致的机制，不继承 generic cross-job lag、运输时间、no-wait 等式或场景专属参数。
