---
id: operator-standard-fjsp-awls-hgtsa-execution-skeleton
type: operator
title: 标准 FJSP AWLS/HGTSA 局部搜索执行骨架
tags: [operator, fjsp, standard-fjsp, agent-generated-solver, awls, n7, n8, nk, k-insertion, tabu-search, critical-path, implementation-skeleton]
source: distilled_from_local_examples_and_operator_cards
status: implementation_skeleton
---

## 目的

本卡适用于已经具备合法 parser、constructor 与 output writer 的 agent 生成
标准 FJSP solver。它描述了在宣称实现 AWLS、N7/N8、NK、k-insertion 或关键路径局
部搜索之前，至少需要具备的可执行结构。它提供的是方法知识，而不是后端 solver 代码。

不要把实例分数、已知最优值或以往解出的排程复制进 solver。生成的 solver 必须从当前
激活的 IO 文档和当前实例文件出发，自行推导每一份 schedule。

## 必需代码形态

真正的 FJSP 局部搜索实现应包含以下相互连通的部分：

1. 稳定的工序标识。
   从 parser 到 constructor、decoder、neighborhood moves、self-check 与输出，
   统一使用 `(job_id, op_id)` 这类 key。加工选项应存放在
   `op_info[(job_id, op_id)]` 或等价映射中。

2. 搜索状态。
   同时维护 `assignment[op] = machine_id` 与
   `machine_sequences[machine_id] = [op, ...]`。单独的 schedule list 不足以支撑
   N7/N8/NK 风格的移动，因为这类移动作用于 machine arc。

3. 完整的 active decoder。
   实现一个 `decode_state(...)` 风格函数，它需要：
   - 验证每道工序恰好出现在某一条 machine sequence 中一次；
   - 验证所分配的机器对该工序是合法候选机；
   - 构建 job-precedence arc 与 same-machine sequence arc；
   - 通过 topological/progress loop 调度工序，而不是按机器顺序整条回放所有工序；
   - 拒绝 cycle、缺失工序、重复工序与部分 schedule；
   - 同时返回完整 schedule 与真实 makespan。

4. 关键路径证据。
   decode 后，应从解码图中计算或近似关键工序/关键块。忽略关键路径的移动，不应被称
   为 N7/N8/AWLS 局部搜索。

5. 同机 N7/N8 移动生成器。
   在关键机器块上生成有界移动，例如相邻交换、块首插入、块尾插入，以及在关键块内部
   或附近对选定关键工序进行重定位。每个移动都必须生成新的
   `assignment + machine_sequences` 状态，再调用完整 decoder。

6. NK / k-insertion 换机重分配。
   对选定关键工序，枚举其它 eligible machine 以及目标机器上的少量插入位置。一个好的
   初始位置集合通常包括：靠近该工序当前时间窗、目标机器关键块附近、最早可行位置以及
   机器尾部。实例较大时，不要把所有可能位置全部 decode；应先对有界 shortlist 打分或
   采样。

7. Tabu 或有界改进循环。
   保持 `current_state/current_makespan` 与 `best_state/best_makespan` 相互独
   立。当前 tabu 步可以是非改善的，但 solver 最终输出必须仍然是已解码的最优
   incumbent。对逆向弧或 return-to-machine move 使用 tabu key；当 tabu move 优于全
   局最优时应用 aspiration，并限制迭代次数、邻居数量与墙钟时间。

## 实现微模板

这些片段有意保持紧凑，并与具体实例无关。它们展示的是 coding agent 可适配到当前
parser 与 output schema 的可复用方法结构，而不是后端编排代码。

### 移动记录与应用

应使用能够同时表达 same-machine N8-like relocation 与 change-machine
k-insertion 的移动对象。不要原地修改 incumbent state。

```python
def apply_move(state, move):
    assignment = dict(state.assignment)
    sequences = {m: list(seq) for m, seq in state.machine_sequences.items()}
    op = move["op"]
    old_machine = assignment[op]

    if op in sequences.get(old_machine, []):
        sequences[old_machine].remove(op)

    new_machine = move.get("to_machine", old_machine)
    assignment[op] = new_machine
    target = sequences.setdefault(new_machine, [])
    pos = max(0, min(move["insert_pos"], len(target)))
    target.insert(pos, op)
    return SearchState(assignment=assignment, machine_sequences=sequences)
```

### 从已解码 schedule 提取关键块

如果 solver 已经具备 `decode_state(...)`，就应从解码时序中提取关键块，而不是移动随
机工序。一个简单的首版可以先识别 zero-slack operations，再把同机连续工序切分为块。

```python
def critical_blocks(decoded, state):
    # decoded.start/end and decoded.tail can be exact or approximate.
    critical = {
        op for op in decoded.ops
        if decoded.start[op] + decoded.duration[op] + decoded.tail[op] == decoded.makespan
    }
    blocks = []
    for machine, seq in state.machine_sequences.items():
        current = []
        for index, op in enumerate(seq):
            if op in critical:
                current.append(index)
            else:
                if len(current) >= 2:
                    blocks.append((machine, current))
                current = []
        if len(current) >= 2:
            blocks.append((machine, current))
    return blocks
```

### 类 N8 的同机候选生成器

N8 不只是随机交换。一个有用的小型版本会围绕关键块及其外侧小窗口移动关键工序，再对
每个候选执行 decode。

```python
def generate_n8_like_neighbors(state, decoded, *, window=3):
    for machine, block in critical_blocks(decoded, state):
        seq = list(state.machine_sequences[machine])
        left, right = block[0], block[-1]
        candidate_indices = set(block)
        candidate_indices.update(range(max(0, left - window), min(len(seq), right + window + 1)))

        for from_pos in block:
            op = seq[from_pos]
            for to_pos in candidate_indices:
                if to_pos == from_pos:
                    continue
                # Skip no-op adjacent reinsertion.
                reduced = [item for idx, item in enumerate(seq) if idx != from_pos]
                insert_pos = to_pos if to_pos < from_pos else to_pos - 1
                if insert_pos < 0 or insert_pos > len(reduced):
                    continue
                yield {
                    "kind": "n8_reinsert",
                    "op": op,
                    "from_machine": machine,
                    "to_machine": machine,
                    "insert_pos": insert_pos,
                    "tabu_key": ("arc", machine, op, seq[max(0, from_pos - 1)] if from_pos else None),
                }
```

### K-Insertion / NK 候选生成器

针对 FJSP 的柔性，应聚焦关键工序，并把它们插入一小组目标机器位置中。由于它利用了关
键性与候选机器替代关系，因此比随机重分配更强。

```python
def insertion_positions_for(machine_seq, op, decoded, *, window=2):
    positions = {0, len(machine_seq)}
    pivot_time = decoded.start.get(op, 0)
    by_time = sorted(
        range(len(machine_seq)),
        key=lambda idx: abs(decoded.start.get(machine_seq[idx], 0) - pivot_time),
    )
    for idx in by_time[:window]:
        positions.update({idx, idx + 1})
    return sorted(pos for pos in positions if 0 <= pos <= len(machine_seq))

def generate_k_insertion_neighbors(state, decoded, op_info, *, max_ops=12):
    critical_ops = sorted(decoded.critical_ops, key=lambda op: decoded.tail.get(op, 0), reverse=True)
    for op in critical_ops[:max_ops]:
        old_machine = state.assignment[op]
        for new_machine, _duration in op_info[op]:
            if new_machine == old_machine:
                continue
            target_seq = list(state.machine_sequences.get(new_machine, []))
            for pos in insertion_positions_for(target_seq, op, decoded):
                yield {
                    "kind": "k_insertion",
                    "op": op,
                    "from_machine": old_machine,
                    "to_machine": new_machine,
                    "insert_pos": pos,
                    "tabu_key": ("machine_return", op, old_machine),
                }
```

### 候选短名单

应只 decode 有界 shortlist，而不是所有可能移动。一个简单 proxy 可以结合 criticality、
目标机器负载与工序时长。proxy 只用于排序候选；最终接受仍必须依据 decode 后的
makespan。

```python
def move_proxy(move, state, decoded, op_info):
    op = move["op"]
    to_machine = move["to_machine"]
    duration = dict(op_info[op])[to_machine]
    target_load = sum(dict(op_info[item])[state.assignment[item]] for item in state.machine_sequences.get(to_machine, []))
    critical_bonus = -decoded.tail.get(op, 0)
    return critical_bonus + duration + 0.05 * target_load

def shortlist_moves(moves, state, decoded, op_info, *, limit=200):
    ranked = sorted(moves, key=lambda move: move_proxy(move, state, decoded, op_info))
    return ranked[:limit]
```

### 带多样化的禁忌循环

纯粹的 first-improvement 爬山搜索往往过于集中，只会在单个盆地内移动。最小
tabu loop 应保持 `current` 与 `best` 分离，允许非改善的当前 move，在出现全局改善时
使用 aspiration，并在停滞后进行 perturb/restart。

```python
def tabu_search(initial_state, decode_state, op_info, rng, deadline):
    current = initial_state
    current_decoded = decode_state(current)
    best = current
    best_decoded = current_decoded
    tabu_until = {}
    no_improve = 0
    iteration = 0

    while time.time() < deadline and iteration < 500:
        moves = []
        moves.extend(generate_n8_like_neighbors(current, current_decoded))
        moves.extend(generate_k_insertion_neighbors(current, current_decoded, op_info))
        rng.shuffle(moves)  # diversification before shortlist ties
        moves = shortlist_moves(moves, current, current_decoded, op_info, limit=150)

        chosen = None
        chosen_decoded = None
        for move in moves:
            candidate = apply_move(current, move)
            decoded = decode_state(candidate)
            if decoded is None:
                continue
            tabu = tabu_until.get(move["tabu_key"], -1) > iteration
            aspiration = decoded.makespan < best_decoded.makespan
            if tabu and not aspiration:
                continue
            if chosen_decoded is None or decoded.makespan < chosen_decoded.makespan:
                chosen = (move, candidate)
                chosen_decoded = decoded

        if chosen is None:
            current = perturb_state(best, rng)  # bounded random reinsert/change-machine moves
            current_decoded = decode_state(current) or best_decoded
            no_improve += 1
            iteration += 1
            continue

        move, current = chosen
        current_decoded = chosen_decoded
        tabu_until[move["tabu_key"]] = iteration + 15

        if current_decoded.makespan < best_decoded.makespan:
            best = current
            best_decoded = current_decoded
            no_improve = 0
        else:
            no_improve += 1

        if no_improve and no_improve % 50 == 0:
            current = perturb_state(best, rng)
            current_decoded = decode_state(current) or best_decoded
        iteration += 1

    return best, best_decoded
```

### 最小扰动

扰动应在不破坏合法性的前提下实现多样化。每次 perturbation 后都要重新 decode；若
decode 失败，应回退到 best state。

```python
def perturb_state(state, rng, *, moves=3):
    candidate = state
    for _ in range(moves):
        machine = rng.choice([m for m, seq in candidate.machine_sequences.items() if len(seq) >= 2])
        seq = list(candidate.machine_sequences[machine])
        op = seq.pop(rng.randrange(len(seq)))
        seq.insert(rng.randrange(len(seq) + 1), op)
        sequences = {m: list(s) for m, s in candidate.machine_sequences.items()}
        sequences[machine] = seq
        candidate = SearchState(assignment=dict(candidate.assignment), machine_sequences=sequences)
    return candidate
```

## 最低自检证据

当 worker 提交 `solver_contract_self_check` 时，证据应指向与该骨架相对应的真实源码符号：

- parser / operation map：例如 `parse_instance`、`op_info`、`all_ops`
- 状态表示：例如 `assignment`、`machine_sequences`、`SearchState`
- decoder：例如 `decode_state`、`predecessors`、`successors`、`ready`、
  `progressed`、`topological_order`
- 邻域：例如 `generate_n8_neighbors`、
  `generate_k_insertion_neighbors`、`apply_move`
- incumbent 保留：例如 `best_state`、`best_schedule`、
  `if decoded is None: continue`、`if candidate_makespan < best_makespan`
- 运行时边界：例如 `deadline`、`max_iterations`、`neighbor_limit`、
  `no_improve_limit`

## 红旗信号

即使文本中提到 AWLS、N7、N8 或 NK，以下情况也应视为浅层或无效的局部搜索：

1. 只修改 dispatch weight、ready-list tie-break 或随机 seed。
2. 移动 schedule 字典，却不重建 machine_sequences。
3. 以 machine-major 顺序回放 `machine_sequences` 并更新 `job_ready`，从而可能在前驱
   完成前调度某作业后继工序。
4. 不经过完整 decode，就原地交换两个输出区间。
5. 把部分、空或死锁候选当成 makespan 为 0 的候选进行比较。
6. 在候选失败或更差后仍替换 `best_schedule`。
7. 声称实现了 k-insertion，却从不枚举替代 eligible machine。
8. 在表示与 decoder 已存在后，仍只使用“只接受改善”的随机爬山搜索；这只有
   intensification，几乎没有 diversification。
9. 把某个从不 decode、或可能覆盖全局最优的扰动称为“diversification”。

## 演进指导

如果当前被 promotion 的 incumbent 只是一个合法构造式 solver，下一步通常最有价值的增
量是：

1. 添加从 incumbent schedule 中提取 `assignment + machine_sequences` 的逻辑；
2. 添加 `decode_state`，并证明它能重现完整合法 schedule；
3. 添加一个有界的 critical-block 同机 move；
4. 添加一个有界的异机插入 move；
5. 添加 tabu memory 与 candidate shortlisting。

如果上一轮已经加入了随机换机爬山搜索，下一轮就不应再添加另一个随机 hill
climber。应按以下方向升级：

1. 从 critical path / critical blocks 中选取工序，而不是对所有工序一视同仁；
2. 用有界 N8 与 k-insertion 候选集替代随机插入位置；
3. 加入 tabu memory、aspiration 与偶发 perturbation，使搜索同时具备
   intensification 与 diversification；
4. 保证最终输出等于已解码的最优 incumbent，而不是最后一个非改善的当前状态。

除非上一轮明确是在修复同一方向，否则每一轮只做其中一个结构性增量。要保留已被
promotion 的 incumbent，并回滚任何 decode 失败或在 Core evaluator 下更差的候选。
