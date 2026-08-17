# 标准 FJSP Agent 生成邻域模板

只有当生成的 solver 已经具备可达的
`assignment + machine_sequences + decode_state(...)` 路径后，才应使用本卡。
这些模板展示的是更强邻域的可执行代码形态。它们不是实例解，也不得硬编码工序、机器或
makespan。

假设状态：

```python
OpKey = tuple[int, int]
assignment: dict[OpKey, int]
machine_sequences: dict[int, list[OpKey]]
schedule: list[dict]  # decoded records with job_id/op_id/machine_id/start/end
```

solver 预先应提供：

- `decode_state(instance, assignment, machine_sequences) -> list[dict] | None`
- `validate_schedule(instance, schedule) -> bool`
- `coverage_ok(instance, schedule) -> bool`
- `makespan(schedule) -> int`
- `clone_state(assignment, machine_sequences)`

## 移动记录

```python
def schedule_by_machine(schedule: list[dict]) -> dict[int, list[dict]]:
    by_machine: dict[int, list[dict]] = {}
    for item in schedule:
        by_machine.setdefault(item["machine_id"], []).append(item)
    for items in by_machine.values():
        items.sort(key=lambda row: (row["start"], row["end"], row["job_id"], row["op_id"]))
    return by_machine


def op_key_of(item: dict) -> OpKey:
    return (item["job_id"], item["op_id"])


def critical_tail_windows(schedule: list[dict]) -> list[dict]:
    """Extract compact machine windows near the current makespan tail.

    This deliberately uses a conservative, portable signal: operations that end
    close to the current makespan plus adjacent operations on the same machine.
    It is not an exact critical-path or critical-block implementation.
    """
    if not schedule:
        return []
    cmax = makespan(schedule)
    blocks: list[dict] = []
    for machine_id, items in schedule_by_machine(schedule).items():
        anchor_positions = [
            index
            for index, item in enumerate(items)
            if item["end"] == cmax or cmax - item["end"] <= max(1, cmax // 20)
        ]
        for pos in anchor_positions:
            left = max(0, pos - 2)
            right = min(len(items), pos + 3)
            block_ops = [op_key_of(item) for item in items[left:right]]
            if len(block_ops) >= 2:
                blocks.append({"machine": machine_id, "ops": block_ops})
    return blocks
```

## 应用移动

```python
def apply_sequence_move(
    assignment: dict[OpKey, int],
    machine_sequences: dict[int, list[OpKey]],
    move: dict,
) -> tuple[dict[OpKey, int], dict[int, list[OpKey]]] | None:
    next_assignment, next_sequences = clone_state(assignment, machine_sequences)
    kind = move.get("kind")

    if kind == "same_machine_swap":
        machine_id = int(move["machine"])
        left = tuple(move["left"])
        right = tuple(move["right"])
        seq = next_sequences.get(machine_id)
        if seq is None or left not in seq or right not in seq:
            return None
        i = seq.index(left)
        j = seq.index(right)
        seq[i], seq[j] = seq[j], seq[i]
        return next_assignment, next_sequences

    if kind == "same_machine_insert":
        machine_id = int(move["machine"])
        op_key = tuple(move["op"])
        pos = int(move["pos"])
        seq = next_sequences.get(machine_id)
        if seq is None or op_key not in seq:
            return None
        seq.remove(op_key)
        pos = max(0, min(pos, len(seq)))
        seq.insert(pos, op_key)
        return next_assignment, next_sequences

    if kind == "change_machine_insert":
        op_key = tuple(move["op"])
        new_machine = int(move["machine"])
        pos = int(move["pos"])
        old_machine = next_assignment.get(op_key)
        if old_machine is None:
            return None
        old_seq = next_sequences.get(old_machine)
        if old_seq is None or op_key not in old_seq:
            return None
        old_seq.remove(op_key)
        new_seq = next_sequences.setdefault(new_machine, [])
        pos = max(0, min(pos, len(new_seq)))
        new_seq.insert(pos, op_key)
        next_assignment[op_key] = new_machine
        return next_assignment, next_sequences

    return None
```

## 关键尾部候选生成

在尚未具备精确析取 DAG 实现前，这个有界生成器很有用。不要把它标记为 N7、N8、
k-insertion 或精确关键块搜索。

```python
def generate_critical_tail_moves(
    instance: dict,
    assignment: dict[OpKey, int],
    machine_sequences: dict[int, list[OpKey]],
    schedule: list[dict],
    *,
    max_moves: int = 200,
) -> list[dict]:
    moves: list[dict] = []
    for block in critical_tail_windows(schedule):
        machine_id = block["machine"]
        block_ops = block["ops"]
        seq = machine_sequences.get(machine_id, [])
        for left, right in zip(block_ops, block_ops[1:]):
            if left in seq and right in seq:
                moves.append({"kind": "same_machine_swap", "machine": machine_id, "left": left, "right": right})
                if len(moves) >= max_moves:
                    return moves
        for op_key in block_ops:
            if op_key not in seq:
                continue
            old_pos = seq.index(op_key)
            for pos in range(max(0, old_pos - 3), min(len(seq), old_pos + 4)):
                if pos != old_pos:
                    moves.append({"kind": "same_machine_insert", "machine": machine_id, "op": op_key, "pos": pos})
                    if len(moves) >= max_moves:
                        return moves
            for new_machine in instance["op_info"][op_key]["eligible"]:
                if new_machine == machine_id:
                    continue
                target_len = len(machine_sequences.get(new_machine, []))
                for pos in (0, target_len // 2, target_len):
                    moves.append({"kind": "change_machine_insert", "op": op_key, "machine": new_machine, "pos": pos})
                    if len(moves) >= max_moves:
                        return moves
    return moves
```

## 禁忌 / 最佳改进搜索循环

```python
def move_signature(move: dict) -> tuple:
    kind = move.get("kind")
    if kind == "same_machine_swap":
        return (kind, int(move["machine"]), tuple(move["left"]), tuple(move["right"]))
    if kind == "same_machine_insert":
        return (kind, int(move["machine"]), tuple(move["op"]), int(move["pos"]))
    if kind == "change_machine_insert":
        return (kind, tuple(move["op"]), int(move["machine"]), int(move["pos"]))
    return (str(kind),)


def reverse_move_signature(
    move: dict,
    assignment: dict[OpKey, int],
    machine_sequences: dict[int, list[OpKey]],
) -> tuple | None:
    """Return the signature of the move that would undo the accepted move."""
    kind = move.get("kind")
    if kind == "same_machine_swap":
        return (kind, int(move["machine"]), tuple(move["right"]), tuple(move["left"]))
    if kind == "same_machine_insert":
        machine_id = int(move["machine"])
        op_key = tuple(move["op"])
        seq = machine_sequences.get(machine_id, [])
        if op_key not in seq:
            return None
        return (kind, machine_id, op_key, seq.index(op_key))
    if kind == "change_machine_insert":
        op_key = tuple(move["op"])
        old_machine = assignment.get(op_key)
        old_seq = machine_sequences.get(old_machine, []) if old_machine is not None else []
        if old_machine is None or op_key not in old_seq:
            return None
        return (kind, op_key, int(old_machine), old_seq.index(op_key))
    return None


def tabu_best_improvement(
    instance: dict,
    assignment: dict[OpKey, int],
    machine_sequences: dict[int, list[OpKey]],
    schedule: list[dict],
    *,
    seed: int,
    deadline: float,
) -> tuple[dict[OpKey, int], dict[int, list[OpKey]], list[dict]]:
    rng = random.Random(seed)
    best_assignment, best_sequences = clone_state(assignment, machine_sequences)
    best_schedule = list(schedule)
    best_value = makespan(best_schedule)
    current_assignment, current_sequences = clone_state(best_assignment, best_sequences)
    current_schedule = list(best_schedule)
    tabu_until: dict[tuple, int] = {}
    no_improve = 0
    iteration = 0

    while time.perf_counter() < deadline and iteration < 1500:
        iteration += 1
        moves = generate_critical_tail_moves(
            instance,
            current_assignment,
            current_sequences,
            current_schedule,
            max_moves=180,
        )
        if not moves:
            break
        rng.shuffle(moves)
        best_trial = None
        best_trial_value = None
        best_reverse_signature = None
        for move in moves:
            signature = move_signature(move)
            reverse_signature = reverse_move_signature(
                move,
                current_assignment,
                current_sequences,
            )
            trial_state = apply_sequence_move(current_assignment, current_sequences, move)
            if trial_state is None:
                continue
            trial_assignment, trial_sequences = trial_state
            trial_schedule = decode_state(instance, trial_assignment, trial_sequences)
            if trial_schedule is None or not validate_schedule(instance, trial_schedule):
                continue
            if not coverage_ok(instance, trial_schedule):
                continue
            trial_value = makespan(trial_schedule)
            tabu = tabu_until.get(signature, -1) > iteration
            aspiration = trial_value < best_value
            if tabu and not aspiration:
                continue
            if best_trial_value is None or trial_value < best_trial_value:
                best_trial_value = trial_value
                best_trial = (trial_assignment, trial_sequences, trial_schedule)
                best_reverse_signature = reverse_signature
        if best_trial is None:
            no_improve += 1
            if no_improve >= 20:
                break
            continue

        current_assignment, current_sequences, current_schedule = best_trial
        if best_reverse_signature is not None:
            tabu_until[best_reverse_signature] = iteration + 9 + (iteration % 5)

        if best_trial_value is not None and best_trial_value < best_value:
            best_assignment, best_sequences = clone_state(current_assignment, current_sequences)
            best_schedule = list(current_schedule)
            best_value = best_trial_value
            no_improve = 0
        else:
            no_improve += 1

    return best_assignment, best_sequences, best_schedule
```

## 接受规则

- 在 `decode_state` 返回完整 schedule 之前，绝不要给移动打分。
- 在 makespan 相同或更差时，绝不要替换 incumbent。
- 上面的关键尾部窗口选择器只是一个有界启发式，不构成精确 critical-path、
  critical-block、N7、N8 或 k-insertion 语义的证明。
- 在选择器真正使用当前有效的 disjunctive DAG、zero-slack operations、tight
  machine arcs，以及该邻域文档化的 feasibility bounds 之前，绝不要宣称实现了精确
  critical-block / N7 / N8 / k-insertion。
- 一个自称 tabu loop 的实现，必须存储逆向 signature，证明在 tenure 到期前无法立即回
  退，并返回全局最优状态。
- 候选上限与 deadline 要足够小，能够通过 evaluator 轻量验证。
