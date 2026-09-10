# 合法之后的质量优化

## 1. 候选评分和最终接受规则分开

令 `C_j` 为完整任务的最后工序结束时间，`W_H=sum(w_j for C_j<=H)`。普通机换型序列中，每个真实紧邻边 `setup(u,v)>0` 计 1 次，不按换型时长累计，也不在 H 截断。若当前评价器有其它换型对象，保持其定义。

```text
BETTER(candidate, incumbent):
    require candidate complete and fixed evaluator accepts
    if W(candidate) != W(incumbent): return W(candidate) > W(incumbent)
    return setups(candidate) < setups(incumbent)
```

重量比较采用输入/评价器精度；可使用定点整数或 Decimal，不能自行给重量加 epsilon 而允许小幅减产。若评价器明确用舍入指标排序，则遵守其排序精度并另存未舍入值。并列候选可以作为探索工作解，incumbent 保留既有合法解。调度评分中的 setup 惩罚只决定探索顺序，不能代替最终词典序。

旧 tuner 的标量 tradeoff、Pareto 推荐、“重量不低于一个阈值就换低 setup 解”与当前目标不同，均不可直接迁移为接受规则。优化不得改变评价器、批容量或硬 Q-time。

## 2. 可迁移机制与实装证据

| 方法 | 改变的决策及原因 | 受影响约束/失败处理 | 证据强度 |
|---|---|---|---|
| 重量密度 | ready 工序派工顺序；`weight / remaining ordinary workload`，优先较少瓶颈负载即可完成的重任务 | 重新构造完整候选；Q 紧迫性必须经合法候选过滤；失败恢复 incumbent | 旧评分已实现；组合实验有效，不保证单独提高密度权重必涨产 |
| 时间窗口前瞻 | 先求所有可行候选最早开始 `s_min`，只比较 `s<=s_min+window` 的候选；允许短等待换来较好重量/连续性 | 不改变硬区间；重新派工会改变全部后继，完整重验 | 旧 `selected_phase_pool` 已实现；不是多步 rollout，窗口越大不保证越好 |
| 换型连续性 | 派工排序奖励真实零 setup 和工艺相似性，惩罚正 setup 事件及其耗时 | 使用真实 setup 表；插入/删除必须检查两侧新邻边；全程计数 | 受控消融中关 family 奖励或 setup 惩罚，两例重量下降、换型增加；具体幅度见来源，不迁移参数 |
| 组批与等待 | 在 ready 同族同机候选中选成员、选择共同起点，有限等待争取更多成员 | 每次变化重新计算 D、最小容量、所有成员 Q、共同日历；拒绝新增成员时保持原批；更改已提交批失败则回滚 | 有限 singleton 与多成员批均有历史完整零错记录；等待/时长策略效果依实例 |
| 替代路线 | 对临界任务更换整个 route，缓解瓶颈或运输，不只改 path_id | 撤销旧路线全部记录和批依赖闭包；重建资格、Q、setup proc_id；失败恢复旧路线全解 | 旧路线预选、force-path 实装；自动冲突时换路线是补齐设计，历史指定任务路径不迁移 |
| 时间槽交换/局部迁移 | 普通同机等时长工序交换时间槽，或把阻塞工序迁到合法其它机器 | 重验双方任务入/出 Q、运输、日历、相邻 setup、受影响后缀；clone 后完整有限验证 | 旧等时长交换及 deepcopy/validator 实装；旧阈值接受规则须替换 |

旧 `process_family` 是 `(seq,is_batch,candidate machine/time tuple)` 的相似性签名，有限批 `batch_family` 是输入 family tuple，两者不是同一个概念。相似性可用于软排序，不能授予合批资格或免除真实换型。

## 3. 从简单启发式开始

首解构造阶段使用“约束少松弛/已启动任务优先，机器最早可完工”的简单顺序；获得合法解后才尝试评分组合，例如：

```text
score = scaled_weight + scaled_weight_density
        + started_bonus + true_zero_setup_bonus + similarity_bonus
        - positive_setup_event_penalty - setup_duration_penalty
        - avoidable_wait_penalty - optimistic_finish_penalty
```

全部系数从新实例尺度产生或在新输入上验证，不携带旧常数。普通机剩余负载为零时单独处理密度，避免除零或让纯批任务得到无限分。有限批可能本身是瓶颈，可改用总剩余负载密度，但这属于要验证的变体。

安全粗尾部估计 `est_C=E_current+sum minimum future processing`；可加有证明不重复的运输/等待下界，不能把任意非相邻 Q 最小间隔直接全加。`est_C<=H` 也不保证最终 C<=H，它忽略竞争与日历。Q slack 小时可优先，但 priority 不修复窗口，空窗必须进入回退。

## 4. 两阶段派工的可迁移形式

旧算法第一阶段倾向预计能在 H 内完成的任务，第二阶段重激活被延后的任务并补齐。迁移时将其理解为分支顺序：

* 第一阶段优先尝试安全下界仍可能进 H 的候选；开工会产生硬最大 Q 义务，不能将已经开始的链无限期挂起。
* 推迟尚未开工且估计越窗的任务通常减少瓶颈挤占。延期只是工作队列标签，不删除任务，不抹掉资源或 Q 约束。
* 第二阶段取消 horizon 候选过滤，仍使用相同有限批、日历、Q 和回退引擎。其后仍可能发现可放进 H 的空档；H 不是分界墙。
* 任一阶段死路均可回退，包括为尾部完整性牺牲第一阶段某个暂定高分安排。只有全部完成并验证通过后，这个候选才有可比较的 W_H。

## 5. 优化事务

```text
best = immutable_copy(first_valid_complete_schedule)
while optimization budget remains:
    proposal = choose_one_operator(best, current_instance_features)
    candidate = clone(best)
    removed = dependency_closure(proposal)   # 见冲突回修
    unschedule(candidate, removed)
    result = bounded_reconstruct(candidate, affected_scope, proposal)
    if failed: discard candidate; continue
    full_report = fixed_evaluator(export_and_reload(result))
    if full_report.complete and full_report.error_count == 0:
        if BETTER(result,best): best = immutable_copy(result)
return best
```

允许增量评价加速筛选，但它不能代替提升 incumbent 前的完整验证。对普通时间槽交换，可只快速计算受影响的邻接边集合：前驱、两个被换工序及后继；边去重以防相邻交换双计。全排程 setup 减少但 W_H 降低必须拒绝。

建议从新解中自动提取 horizon 附近未完成的高重量任务、紧 Q 链、负载高机器及换型频繁段，形成局部候选。不要把旧实例的任务名单/奖励表复制到新求解器。每次只改一个机制或有明确组合假设的一组机制，保留正反实验报告；旧两例消融只能支持“值得试”，不是新实例收益保证。
