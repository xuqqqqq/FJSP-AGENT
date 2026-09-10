# 首个完整合法解：从候选到联合安置

## 1. 旧实现与独立构造器的关系

**[实装]** 旧算法先固定路线；`evaluate_candidate` 计算前驱运输和全部 incoming Q 边的上下界，接上机器尾部的换型间隔，再将加工移出维修；候选评分只在这个过滤后执行。它是机器**尾部追加**，不是任意空档插入。有限批通过一个锚点扩展同族成员；第二阶段继续排 H 之后的任务。

**[补齐设计]** 以下把这个机制推广为有失败原因的区间枚举，加上机器链可达性、双侧换型、批时长重算及回退。首轮优先只安排一个成员的有限批：这是容量合法的 singleton batch，不是放松有限批。但 singleton 不保证保留原题可行性：多个成员被硬窗口迫使同时占用同一批机时，可能必须合批才能得到首解。因此首轮失败后，构造搜索须开放同族 ready 成员集合的批分支，按第 4 节计算共同窗口。先做不截断 horizon 的完整构造，优化阶段再采用前瞻和两阶段偏好。

## 2. 普通工序的时间区间

设待排工序 v 固定候选机器 m，加工时长 d，开始 s，结束 s+d。事件值 `T(op,start)=S_op`，`T(op,end)=E_op`。

初始 `L=max(current_time, release_job, propagated_LB(S_v))`，`U=propagated_UB(S_v)`。释放与有限日历以真实输入为准，U 无显式界时为无穷而非 H。

若工艺前驱 p 已排，先检查**p 的有向离厂许可**。通过后：

`L=max(L, E_p+transport(machine_p,m))`。

局部重排时若工艺后继 q 固定，也检查 v 的离厂许可，并加：

`U=min(U, S_q-transport(m,machine_q)-d)`。

对每条 `l <= T(b,beta)-T(a,alpha) <= u`：

* v=b 且 a 已固定，令 A=T(a,alpha)，o=d 若 beta=end，否则 0：`L=max(L,A+l-o)`，`U=min(U,A+u-o)`，省略不存在的界。
* v=a 且 b 已固定，令 B=T(b,beta)，o=d 若 alpha=end，否则 0：`L=max(L,B-u-o)`，`U=min(U,B-l-o)`，同样省略缺失界。
* 两端都未固定：保留时间约束边做传播，不能当作已经满足。
* 同工序两端的 Q 约束直接化为关于 d 或 0 的检查。

每个界保存来源 `(rule, op/edge, anchor_value)`。把多个限制真正取交集；遇到 `L>U` 返回边界来源，不能用延迟/奖励掩盖。

## 3. 机器空档、双侧换型、日历的交集

遍历 m 上相邻占用块 `(P,N)` 之间的每个空档，包括首部和尾部。基础模型为普通工序、相邻换型只要求时间间隔：

```text
l_gap = max(L, E_P + setup(P,v))    # 无 P：只有 current_time 下界，initial setup 按评价器
u_gap = min(U, S_N - setup(v,N) - d) # 无 N：无这一上界
```

必须同时检查 `P->v` 和 `v->N`，并替换旧 `P->N` 边。换型不满足三角不等式，删除/插入也可能把原本合法的间隔变成非法。

严格不可用区间 `[a,b)` 排除 `s<b AND s+d>a` 的开始时间。因此禁止起点区间是开区间 `(a-d,b)`；整数 tick 下为 `[a-d+1,b-1]`。把所有禁止区间从 `[l_gap,u_gap]` 中减掉，得到**若干可行闭区间的并集**，不能只存一个跨越维修的 `[min,max]`。等价地将机器可用段 `[a,b)` 变成起点段 `[a,b-d]` 后取交集。允许 `s+d=a` 或 `s=b`。

若只求每空档最早点，扫描日历，冲突则令 `s=b`，继续扫描并重查 `s<=u_gap`；若需要回退时延迟前段，还应保留整个起点区间以枚举较晚 s。

批块基础口径不计 setup，间隔为零但仍占机器。如果新评价器对批块有换型规则，必须给出块级换型身份。若评价器计分跳过批块而在普通工序间建立换型边，额外检查这些边，不可直接把批块当作“换型清零器”。

**显式换型活动的变体**：若换型段必须连续紧贴 v 且避开日历，设 `q=setup(P,v)`，同时要求 `[s-q,s)` 在允许日历内；相当于检查整个 `[s-q,s+d)` 并要求 `s-q>=E_P`。若 setup 可提前且不可跨维修，另枚举 setup 起点 t，要求 `t>=E_P, t+q<=s`，把 `[t,t+q)` 当资源占用；对固定 N 的入场换型也要重新安置。基础“留 gap”公式不足以证明这一变体。

## 4. 有限批的联合窗口

对同机候选成员集合 B，只从 ready 的不同任务取工序，避免把同任务有先后的两道工序塞入同批。要求每个成员对 m 有资格且为批工序，`family_i == family_anchor` 为完整 tuple 相等。

```text
cap(B) = min_i capacity_i(m)
|B| <= cap(B)
D(B) = max_i p_i(m)
S_i = S_B; E_i = S_B + D(B), for every i
```

对每位成员，**以 D(B) 作为其实际持续时间**重新计算第 2 节的边界，包括已固定端点的入/出 Q、前后工序运输、release。取 `L_B=max_i L_i(D)`，`U_B=min_i U_i(D)`，再与共同机器空档和用 D 计算的日历起点段相交。

有批内两成员之间的时间边时，在相同 S/E 上直接代入检查。未固定下游的所有 Q 边用共享 E 重新传播。增加最长成员后，所有目标 end 锚点的上下界都会减去新的 D；不能只检查旧 upper_bound，不能取原有单件 start 的最大值就宣布可行。

建议初次组批从 singleton 出发逐个试加成员：在临时对象中算新 B/D/cap/窗口；可行才替换临时对象，失败保持原批。批最终提交是一次原子事务。已提交批扩员属于优化动作：必须连同全部成员及受影响后继一起重验。

## 5. 一次安置的伪代码

```text
PLACEMENTS(state, ready_op, route, machine, members={ready_op}):
    if machine 不合资格或任何固定前/后驱的有向转运不许可:
        return empty, reason=qualification/transfer
    if machine 无后续可达机器链: return empty, reason=downstream_machine
    D = max member duration if batch else duration(op,machine)
    if batch family/capacity 不成立: return empty, reason=batch
    [L,U], causes = INTERSECT_MEMBER_EVENT_BOUNDS(state,members,machine,D)
    if L>U: return empty, conflict=(causes(L),causes(U))
    result = []
    for chronological gap (P,N) in state.timeline[machine]:
        [l,u] = intersect([L,U], predecessor_and_successor_setup_bounds(P,N,D))
        intervals = subtract_calendar_forbidden_starts([l,u],D)
        result += (machine,gap,members,D,intervals,causes)
    if result empty: return empty, all_gap_calendar_conflicts
    return result                         # 纯查询，不改变 State

TRY_PLACE(state, choice):
    trial = deep_copy(state)
    instantiate start/end, route and machine decisions in trial
    replace affected machine adjacency edges
    insert one activity block; add all member records atomically
    propagate temporal bounds for all affected Q/precedence edges
    if contradiction or any affected fixed constraint fails: return failure
    refresh ready, next_index, completion, setup edges; clear stale cache
    assert state invariants(trial)
    return trial
```

所有空档都失败不等于实例不可行，可能是当前前段机器/开始时间已选错。把失败原因交给回修模块。

## 6. 从空状态到完整解

```text
CONSTRUCT(input, evaluator, budget):
    model = parse_and_freeze_contract(input)
    retain all alternative routes; prune provably unreachable machine chains
    route order = proxy workload ranking, stable structural tie breaking
    state = empty timelines/records + current_time/release/calendar constants
    # 首解不按 horizon 排除候选；优先 singleton，失败时允许有限多成员批分支
    result = BOUNDED_SEARCH(state, route_order, budget)  # 见回修文档完整循环
    if result has no complete state: return BUDGET_EXHAUSTED or proven_infeasible
    export all tasks on exactly one route; round-trip reload
    report = fixed_evaluator(export)
    if report rejects: diagnose concrete mismatch; fix within budget and rerun
    else: save immutable complete incumbent with recomputed (W_H,N_setup)
```

搜索分支先选待排任务/路线，再枚举其 ready 工序所有机器、空档及开始点。建议初版优先把一个已开始任务排完，缩小未闭合硬 Q 链；但这只是分支顺序，失败时允许回退此前任务。优先最早完工、运输短、后续可达性强的机器，不能固定“加工最快”而不检查下游。

仅存最早开始方案会漏掉“上游晚开才能赶上下游维修后窗口”的解。需要保留较晚起点的分支或反向传播生成这些起点。horizon 不用作可行性搜索时间上限；搜索上限 T_search 是独立预算边界，应允许延长。有限预算启发式没有任意可行实例的成功保证，不能将失败称为不可行证明。
