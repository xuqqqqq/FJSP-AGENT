# FJSP 核心伪代码

这些片段用于理解和重构，不是必须照抄的框架。适配项目已有数据结构、求解器 API 和冻结的时间边界，并保留可行性、incumbent 与 deadline 不变量。

## 目录

- [按结构选择研究方向](#1-按结构选择研究方向)
- [解码机器分配与机器顺序](#2-解码机器分配与机器顺序)
- [带绝对时间上限的分层求解](#3-带绝对时间上限的分层求解)
- [生成有界关键邻域](#4-生成有界关键邻域)
- [分配信任域](#5-分配信任域)
- [探测和最终入口选择](#6-探测和最终入口选择)

## 1. 按结构选择研究方向

```python
def characterize(problem, incumbent=None):
    alternatives = [len(op.alternatives) for op in problem.operations]
    duration_spreads = [
        max(alt.duration for alt in op.alternatives)
        - min(alt.duration for alt in op.alternatives)
        for op in problem.operations
    ]

    features = {
        "average_alternatives": mean(alternatives),
        "flexible_operation_ratio": mean(count > 1 for count in alternatives),
        "average_duration_spread": mean(duration_spreads),
        "operation_count": len(problem.operations),
    }
    if incumbent is not None:
        features.update(critical_structure_features(problem, incumbent))
        features.update(machine_load_features(problem, incumbent))
    return features
```

用这些特征提出和比较算法方向，不要返回固定的全局策略枚举。阈值只能是当前语料和实验的可调整起点。

## 2. 解码机器分配与机器顺序

建立同时包含作业优先边和相邻机器顺序边的图，用拓扑最长路计算最早开始时间。

```python
def decode(problem, assignment, machine_orders):
    successors = {op: [] for op in problem.operations}
    indegree = {op: 0 for op in problem.operations}

    def add_edge(before, after):
        successors[before].append(after)
        indegree[after] += 1

    for job in problem.jobs:
        for before, after in adjacent_pairs(job.operations):
            add_edge(before, after)

    for machine, order in enumerate(machine_orders):
        for op in order:
            if assignment[op].machine != machine:
                return None
        for before, after in adjacent_pairs(order):
            add_edge(before, after)

    ready = priority_queue(op for op in problem.operations if indegree[op] == 0)
    earliest_start = {op: 0 for op in problem.operations}
    schedule = []

    while ready:
        op = ready.pop()
        start = earliest_start[op]
        finish = start + assignment[op].duration
        schedule.append((op, assignment[op].machine, start, finish))

        for next_op in successors[op]:
            earliest_start[next_op] = max(earliest_start[next_op], finish)
            indegree[next_op] -= 1
            if indegree[next_op] == 0:
                ready.push(next_op)

    if len(schedule) != len(problem.operations):
        return None

    return Solution(
        makespan=max(finish for _, _, _, finish in schedule),
        schedule=schedule,
    )
```

机器分配或顺序改变后必须重新解码，不能沿用旧的开始时间为 move 打分。

## 3. 带绝对时间上限的分层求解

```python
def solve(problem, time_limit):
    deadline = monotonic_time() + time_limit - SAFETY_MARGIN
    features = characterize(problem)

    bases = construct_diverse_bases(problem, features, deadline)
    incumbent = best_feasible(bases)
    pool = unique_by_search_fingerprint(bases)

    if enough_time(deadline, ENTRY_RESERVE + FINAL_RESERVE):
        entries = generate_entries(problem, pool, incumbent, features, deadline)
        pool = merge_unique(pool, entries)
        incumbent = best_feasible([incumbent, *entries])

    if enough_time(deadline, PROBE_RESERVE + FINAL_RESERVE):
        probe_rows = short_cp_probes(
            problem,
            select_probe_candidates(pool, incumbent),
            deadline,
        )
        incumbent = best_feasible([
            incumbent,
            *(row.improved for row in probe_rows),
        ])
    else:
        probe_rows = []

    final_entries = select_final_entries(pool, incumbent, probe_rows)
    for entry, seconds in allocate_final_attempts(final_entries, deadline):
        incumbent = best_feasible([
            incumbent,
            full_cp_polish(problem, entry, seconds),
        ])

    return validate_or_fallback(incumbent, best_feasible(bases))
```

每一阶段都要可中断。始终预留序列化、验证和返回 incumbent 的时间。

## 4. 生成有界关键邻域

```python
def critical_neighborhood(problem, solution, limits):
    decoded = decode_solution(problem, solution)
    blocks = critical_machine_blocks(decoded)
    rows = []

    for machine, block in rank_blocks(blocks)[:limits.block_count]:
        order = decoded.machine_orders[machine]
        moves = []
        moves += adjacent_swaps_at_boundaries(order, block)
        moves += bounded_insertions(order, block, limits.position_count)
        moves += short_block_reorders(order, block)

        for op in flexible_near_critical_operations(block, decoded):
            moves += credible_alternative_machine_moves(
                op,
                problem,
                limits.alternative_count,
            )

        for move in moves[:limits.moves_per_block]:
            assignment, machine_orders = apply_move(decoded, move)
            candidate = decode(problem, assignment, machine_orders)
            if candidate is not None:
                rows.append((
                    neighborhood_key(candidate, solution, decoded),
                    candidate,
                ))

    return select_structurally_diverse(sorted(rows), limits.entry_count)
```

生成入口而不是复制完整 tabu 轨迹。通过限制关键块、位置、替代机器和总入口数保持预算可解释。

## 5. 分配信任域

```python
def trust_region_cp(problem, entry, candidate_ops, max_changes, seconds):
    model = CpModel()

    for op in problem.operations:
        alternatives = create_optional_intervals(model, op)
        model.add_exactly_one(alt.present for alt in alternatives)

    add_job_precedence_constraints(model, problem.jobs)
    add_machine_no_overlap_constraints(model, problem.machines)

    changed_literals = []
    for op in problem.operations:
        hinted_machine = entry.assignment[op].machine
        if op not in candidate_ops:
            force_machine(model, op, hinted_machine)
            continue

        changed = model.new_bool_var(f"changed_{op}")
        link_changed_to_machine_choice(model, op, hinted_machine, changed)
        changed_literals.append(changed)

    model.add(sum(changed_literals) <= max_changes)
    add_schedule_hints(model, entry)
    model.minimize(makespan_var(model))
    return solve_cp(model, seconds=seconds, fallback=entry)
```

`candidate_ops` 优先来自关键、近关键、过载机器或具有竞争性替代机器的工序。区域要足够小以利用入口，同时允许真正改变盆地。

## 6. 探测和最终入口选择

```python
def short_cp_probes(problem, entries, deadline):
    rows = []
    for entry in entries:
        if not enough_time(deadline, FINAL_RESERVE + PROBE_SECONDS):
            break
        improved = relaxed_full_cp(problem, entry, PROBE_SECONDS)
        rows.append(ProbeRow(
            raw=entry,
            improved=improved,
            gain=entry.makespan - improved.makespan,
            distance=fingerprint_distance(entry, improved),
            features=characterize(problem, improved),
        ))
    return rows


def select_final_entries(pool, incumbent, probes, limit=3):
    selected = [incumbent]
    near_gap = max(ABS_NEAR_GAP, incumbent.makespan * REL_NEAR_GAP)

    responsive = [
        row for row in probes
        if row.improved.makespan <= incumbent.makespan + near_gap
    ]
    responsive.sort(key=lambda row: (
        gap_bucket(row.improved.makespan - incumbent.makespan),
        critical_structure_penalty(row.features),
        load_penalty(row.features),
        -row.gain,
        -row.distance,
    ))

    for row in responsive:
        add_if_fingerprint_diverse(selected, row.improved)
        if len(selected) == limit:
            return selected

    for entry in rank_structurally_diverse(pool, anchor=incumbent):
        add_if_fingerprint_diverse(selected, entry)
        if len(selected) == limit:
            break
    return selected
```

主要最终入口应来自 incumbent，或探测后已接近 incumbent 的候选。不要因为一个很差入口的短期增益大，就把最大预算交给它。
