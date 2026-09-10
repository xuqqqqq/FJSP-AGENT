# 冲突回修：撤销什么、改什么、何时停止

## 1. 证据边界

**[实装]** 旧 `repair_incomplete_tasks` 删除不完整任务的整条已排前缀，保留完整任务原位，重建机器尾状态，再运行补齐阶段；完整任务数量不再增长或轮数耗尽则停。旧 `repair_setup_violations` 会删除新相邻换型冲突两侧的任务再排。

这确实包含资源撤销，但不是下述冲突导向搜索。旧代码无通用决策栈、前段时窗反推、替代路线回退或整批依赖闭包；函数返回也不保证完整合法。

**[补齐设计]** 以下提供可实现且可终止的初版：深拷贝快照 + 有限分支 DFS。先把这个版本做对，再考虑撤销日志和冲突回跳。它保持每个接受的部分状态合法，但不保证有限预算内找到所有可行实例的解。

## 2. 保留失败原因

候选生成返回结构化失败，至少包含：

```text
Conflict:
  op, route, attempted_machine
  category: qualification | transfer | Q_interval | calendar | machine_gap | batch | propagation
  lower = {value, originating constraints}
  upper = {value, originating constraints}
  blocking_calendar_or_activity_ids
  relevant_decisions = {route/machine/start/batch decision indices}
```

同工序所有候选都失败时，合并这些原因。无资格机器先检查路线/解析；跨厂失败检查前一工序机器及前一工序许可；Q 空窗追踪其源事件；日历失败同时记下维修后最早起点与 Q 上界。这个集合是“已知冲突”，不是声称数学最小不可行核。

若失败原因都来自固定输入、与可撤销决策无关，可排除当前路线。例如分层机器图无完整路径。但排除了当前路线不代表其它路线不可行。

## 3. 下游被迫晚开时如何反推前段

假设下游 b 在选定机器空档/维修后最早开始为 t，实际持续时间 D_b。有限最大 Q 约束为：

`T(b,beta) - T(a,alpha) <= u`。

此分支需要 `T(a,alpha) >= t + offset(beta,D_b) - u`。例如 `E_a -> S_b <= 4`，b 最早在 20 开始，则要求 `E_a >= 16`。把这条新下界作用到 a 的源事件；若 a 持续 5，要求 `S_a>=11`。撤销 a 及受依赖影响的后缀后，重新选择 a 的较晚起点或另一个机器，随后重排 b。

如果 a 还受另一个上界限制，新的下界必须继续和它相交。无交集就换下游机器/空档，或回退更早决策/路线。不能直接把 a 的记录加一个 delta 而保留原机器占用和后继记录。

同理，有限下界 `T_b-T_a>=l` 在 b 固定时给 `T_a<=T_b-l`；向后移 b 可能解决下界，却更恶化另一条最大 Q。每次都重算全部入/出边。

**反推 t 依赖当前机器空档选择**。不能因某台机器维修结束在 20 就给所有分支永久加 `E_a>=16`；另一台机器可能允许 b 在 8 开始。此界只能保存为该机器/空档分支的提示，或用它给 a 新增一个候选起点；在不固定此下游选择时，不能作为全局剪枝。

整条路线统一平移 delta 后，内部差值满足 `(T_b+delta)-(T_a+delta)=T_b-T_a`。因此路线整体平移不能修复已违反的内部 Q 间隔。推迟整条路线后**重新构造内部相对时间**可以产生不同解，原因是重排而不是平移本身。

## 4. 时间图传播：让非相邻约束参与回修

推荐为每条当前选定路线建事件变量 `S_i,E_i`。普通工序机器选定后添加 `E_i-S_i=p_i(m)`；批成员共用 S/E 和批最大时长 D。普通工序或已固定为 singleton 的工序，在未选机器时可用候选 duration 的 `[min,max]` 作安全松弛，绝不能暂定最短 duration 并据此永久删除可行路线。对于尚未决定成员的批工序，单件最大 duration 不是实际批时长的安全上界；应只用单件最短时长下界，上界用无穷或可证明的同族合法合批最大时长。拟合批时替换旧 duration 约束为共同 D，并从未施加旧约束的快照重传播，不能把 `duration=旧单件时长` 与 `duration=D` 同时保留。

普通先后关系为 `S_next-E_prev>=transport`。运输未决定时只用合法剩余机器对中的最小值作松弛，选定机器再收紧。每条 Q 都独立加 `l<=T_b-T_a<=u`，不要求相邻。

把 `x_v-x_u<=c` 视作差分约束边；双侧约束拆成两条，固定事件用与时间原点的上下界表示。可实现 Bellman–Ford 负环检测；所有节点接超级源避免漏掉不连通分量。负环表明**当前离散选择**下的时间约束矛盾。机器日历/次序是析取条件，不可在尚未选择空档时将其中一侧强加给所有分支。

也可先用工作队列传播下/上界：对于 `x_v>=x_u+c`，执行 `LB_v=max(LB_v,LB_u+c)` 和 `UB_u=min(UB_u,UB_v-c)`，有变更就将邻边入队；`LB>UB` 报冲突。仅靠无限迭代至固定点可能卡在矛盾环，所以要检测环或设置传播步数上限，超限返回 UNKNOWN 而非“可行”。成功传播只是必要条件；还须具体安置和检查所有资源约束。

## 5. 有界搜索的完整控制流

为了使终止有定义：设置 wall time、节点数、最大栈深、重启次数、离散时间步长和有限 `T_search`。这里 `T_search>H` 可逐轮扩大；它只是本轮搜索边界，不是交付截止约束。时间为整数 tick 时，给定 T_search 和有限机器/路线/任务，搜索树有限。连续时间场景只枚举有限事件点时是不完备启发式，不能声称穷尽。

`ENUM_CHOICES` 在固定前缀下输出路线选择，或一项 ready 工序的 `(machine,gap,start,members)` 决策。优先 singleton；失败时开放同机同族 ready 成员的有限子集分支（人数不超过最紧容量），重新计算共同 D 与窗口。贪心逐个加成员是优先顺序，不是对所有可行批集合的穷尽；需要时回退成员组合。先按启发式枚举每个可行区间最早点、维修边界/Q 反推事件点，之后可在预算允许时枚举区间内尚未试过的整数点。跨工件 ready 选择也属于分支；始终固定一个任务顺序会损失搜索能力。

```text
BOUNDED_SEARCH(initial, route_order, limits):
    counters = new_global_counters()             # 不随快照恢复
    for restart in finite_restart_policy(limits):
        # 新 T_search/排序可以改变，本轮输入硬约束不变
        state = deep_copy(initial)
        stack = [FRAME(state, ENUM_CHOICES(state, restart), depth=0)]
        while stack not empty and budget_remaining(counters, limits):
            frame = stack.top
            if frame.choices exhausted:
                stack.pop()                     # 下一轮从父快照再试，不保留子状态
                continue
            choice = frame.choices.next_untried()
            counters.nodes += 1
            state = deep_copy(frame.before)     # 每一次 sibling 都从相同前缀开始
            trial, conflict = TRY_PLACE_OR_SELECT_ROUTE(state, choice)
            if trial failed:
                frame.failures.add(choice.signature, conflict)
                add_branch_local_event_hints_if_useful(stack, conflict)
                continue
            if all jobs complete(trial):
                result = export_reload_and_fixed_validate(trial)
                if result.valid_and_complete:
                    return SUCCESS(trial, result)
                # 判定器定位到已生成逻辑缺陷，保留反例；不以错误指标接受
                frame.failures.add(choice.signature, result.errors)
                continue
            if frame.depth + 1 >= limits.max_depth:
                record budget cutoff; continue
            choices = ENUM_CHOICES(trial, restart)
            if choices empty:
                conflict = aggregate_all_failed_candidates(trial)
                add_branch_local_event_hints_if_useful(stack, conflict)
                continue
            stack.push(FRAME(deep_copy(trial), choices, frame.depth+1))
        if global wall/node budget exhausted: break
    return NOT_FOUND_WITHIN_BUDGET(conflicts, attempted_ranges)
```

`FRAME` 保存：不可变 before 快照、候选迭代器的游标、已尝试签名集合、失败原因、深度。最大深度至少容纳“所有路线选择 + 所有工序/批提交”；不足时必须标注 budget cutoff。有限候选被耗尽只能证明该受限搜索域无解；只有全部路线/机器/整数起点/次序在一个有理论完备上界的域中被穷尽，才有资格声称不可行。

上面的 `add_branch_local_event_hints_if_useful` 可先实现为空操作，靠整数起点枚举和逐层回退仍能探索延迟前段。优化版本找到涉及 a 的 frame，在该 frame 的 before 快照下重新算机器和空档，加入 `max(interval.L, required_source_start)`，仅当在区间内且未试过才入候选。提示不得修改其它 frame 的快照，也不能重复复活已试分支。常规逐层回退始终是正确的后备路径。

开始点、机器选择、路线选择都可能失败，回退顺序由栈决定：同机另一开始/空档 → 同工序其它机器 → 更早工序决策 → route → 之前工件/派工顺序。可把冲突相关分支排在前面，但初版不需要不安全的非时序回跳。

## 6. 快照恢复规则

首版直接恢复整个 before 深拷贝，恢复第一个被撤销决策以前的一切状态；所有之后的工序与批提交一起消失。这个方案避免漏掉跨任务的批依赖。

恢复后必须等价于从 before.records 重建：

* timeline 的块、顺序、邻接换型边和机器尾信息一致；删除中间块后要恢复原来的邻边，而不是沿用候选的计数。
* records、batch members、公共 S/E/D/cap、next_index、ready、job_status、completion 一致。
* 时间图和传播域恢复，撤销分支产生的下界不能泄漏到下个分支。
* setup_count、W_H 和缓存恢复；计分缓存按版本作废；随机状态和批 ID 生成器恢复。
* 搜索预算、已经尝试的分支签名不恢复；incumbent 不受本轮恢复影响。

使用 undo journal 后，日志记录每次实际修改的旧值，并按逆序恢复；禁止只记录 `machine_free`。每次事务提交/失败做小实例深拷贝等价性检查，确认后才切换高性能实现。

## 7. 局部资源撤销的闭包（合法解之后的加速方案）

全栈回退足够构造首解。需要局部重排时，以要改变的工序或批为撤销种子 R：

1. 加入同任务从最早受影响工序起的全部已排后缀；更早的非相邻 Q 源若也需移动，向前扩展切点。
2. R 与一个批相交时，初版把整批成员全部加入 R；再加入各成员的任务后缀，迭代到闭包不再增长。不能删除最长成员后让幸存成员沿用旧 D。
3. 删除 R 涉及的机器块，保留外部占用原位，重新检查“被删除块两侧现在相邻”的 setup。新邻边不合法时，可将相邻任务作为扩展种子，或放弃此邻域并恢复快照。资源不必整体向后推。
4. 以剩余固定记录重新建立时间边界，枚举插入空档及其前后 setup。固定区边界的全部 Q/运输必须检查，不只检查 R 内部。
5. 每次扩大闭包也要受局部任务数/节点/时间预算约束；超过预算直接回滚整次邻域。

如果选择保留批中部分成员，必须重算 `max p`、共同结束时间、容量、所有剩余成员的后续 Q/运输/机器占用；这通常比整批撤销复杂。首版用整批原子撤销更可靠。

终止：所有任务完整且固定评价器通过 → 成功；当前局部邻域耗尽/超时 → 恢复完整 incumbent；首解搜索预算耗尽 → 返回带冲突证据的 NOT_FOUND_WITHIN_BUDGET。不存在“硬 Q-time 改惩罚后继续”这一分支。
