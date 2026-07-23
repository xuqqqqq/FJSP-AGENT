---
id: operator-standard-fjsp-awls-hgtsa-execution-skeleton
type: operator
title: Standard FJSP AWLS/HGTSA Local-Search Execution Skeleton
tags: [operator, fjsp, standard-fjsp, agent-generated-solver, awls, n7, n8, nk, k-insertion, tabu-search, critical-path, implementation-skeleton]
source: distilled_from_local_examples_and_operator_cards
status: implementation_skeleton
---

## Purpose

This card is for an agent-generated standard FJSP solver after it already has a
legal parser, constructor, and output writer. It describes the minimum
executable structure needed before claiming AWLS, N7/N8, NK, k-insertion, or
critical-path local search. It is method knowledge, not backend solver code.

Do not copy instance scores, known optima, or previous solution schedules into a
solver. The generated solver must derive every schedule from the active IO
document and the current instance file.

## Required Code Shape

A real FJSP local-search implementation should have these connected pieces:

1. Stable operation identity.
   Use one key such as `(job_id, op_id)` from parser through constructor,
   decoder, neighborhood moves, self-check, and output. Store processing options
   in `op_info[(job_id, op_id)]` or an equivalent map.

2. Search state.
   Maintain both `assignment[op] = machine_id` and
   `machine_sequences[machine_id] = [op, ...]`. A schedule list alone is not
   enough for N7/N8/NK style moves because the move is about machine arcs.

3. Full active decoder.
   Implement a `decode_state(...)`-style function that:
   - verifies every operation appears exactly once in one machine sequence;
   - verifies the assigned machine is eligible for that operation;
   - builds job-precedence arcs and same-machine sequence arcs;
   - schedules operations by a topological/progress loop, not by replaying all
     operations machine by machine;
   - rejects cycles, missing operations, duplicate operations, and partial
     schedules;
   - returns both a full schedule and a true makespan.

4. Critical-path evidence.
   After decoding, compute or approximate critical operations/critical blocks
   from the decoded graph. A move that ignores the critical path should not be
   called N7/N8/AWLS local search.

5. Same-machine N7/N8 move generator.
   Generate bounded moves on critical machine blocks, such as adjacent exchange,
   block-head insertion, block-tail insertion, and selected critical operation
   relocation within or near the critical block. Each move must create a new
   `assignment + machine_sequences` state and then call the full decoder.

6. NK / k-insertion machine reassignment.
   For selected critical operations, enumerate other eligible machines and a
   small set of insertion positions on the target machine. Good first position
   sets are near the operation's current time window, around target-machine
   critical blocks, the earliest feasible position, and the machine tail. Do not
   decode every possible position when the instance is large; score or sample a
   bounded shortlist first.

7. Tabu or bounded improvement loop.
   Keep `current_state/current_makespan` separate from
   `best_state/best_makespan`. It is acceptable for the current tabu step to be
   non-improving, but the emitted solver result must remain the best decoded
   incumbent. Use tabu keys for reverse arcs or return-to-machine moves, apply
   aspiration when a tabu move beats the global best, and cap iterations,
   neighbor count, and wall-clock time.

## Implementation Micro-Templates

These snippets are intentionally compact and instance-neutral. They are examples
of reusable method structure that a coding agent can adapt to the active parser
and output schema. They are not backend orchestration code.

### Move Record And Application

Use a move object that can express both same-machine N8-like relocation and
change-machine k-insertion. Do not mutate the incumbent state in place.

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

### Critical Blocks From A Decoded Schedule

If a solver already has `decode_state(...)`, it should derive critical blocks
from decoded timing instead of moving random operations. A simple first version
can identify zero-slack operations, then split consecutive operations on the
same machine.

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

### N8-Like Same-Machine Candidate Generator

N8 is not just a random swap. A useful small version moves critical operations
around critical blocks and a small outside window, then decodes each candidate.

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

### K-Insertion / NK Candidate Generator

For FJSP flexibility, focus on critical operations and insert them into a small
set of target-machine positions. This is stronger than random reassignment
because it uses criticality and candidate-machine alternatives.

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

### Candidate Shortlist

Decode a bounded shortlist, not every possible move. A simple proxy can combine
criticality, target-machine load, and operation duration. The proxy only orders
candidates; final acceptance must use decoded makespan.

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

### Tabu Loop With Diversification

Pure first-improvement hill climbing is usually too concentrated: it only moves
inside one basin. A minimal tabu loop should keep `current` and `best` separate,
allow non-improving current moves, use aspiration for global improvement, and
perturb/restart after stagnation.

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

### Minimal Perturbation

Perturbation should diversify without destroying legality. Always decode after
perturbation and fall back to the best state if decoding fails.

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

## Minimum Self-Check Evidence

When the worker submits `solver_contract_self_check`, the evidence should name
real source symbols corresponding to this skeleton:

- parser/operation map: e.g. `parse_instance`, `op_info`, `all_ops`;
- state representation: e.g. `assignment`, `machine_sequences`, `SearchState`;
- decoder: e.g. `decode_state`, `predecessors`, `successors`, `ready`,
  `progressed`, `topological_order`;
- neighborhoods: e.g. `generate_n8_neighbors`,
  `generate_k_insertion_neighbors`, `apply_move`;
- incumbent preservation: e.g. `best_state`, `best_schedule`,
  `if decoded is None: continue`, `if candidate_makespan < best_makespan`;
- runtime bounds: e.g. `deadline`, `max_iterations`, `neighbor_limit`,
  `no_improve_limit`.

## Red Flags

Treat these as shallow or invalid local search even if the text mentions AWLS,
N7, N8, or NK:

1. Only changing dispatch weights, ready-list tie-breaks, or random seeds.
2. Moving schedule dictionaries without rebuilding machine sequences.
3. Replaying `machine_sequences` in machine-major order and updating
   `job_ready`, which can schedule a job successor before its predecessor.
4. Swapping two output intervals in-place without a full decode.
5. Comparing a partial, empty, or deadlocked candidate as if it had makespan 0.
6. Replacing `best_schedule` after a failed or worse candidate.
7. Claiming k-insertion while never enumerating alternative eligible machines.
8. Using only improving random hill climbing after the representation and
   decoder already exist; this has intensification but almost no diversification.
9. Calling a perturbation "diversification" while it is never decoded or can
   overwrite the global best.

## Evolution Guidance

If the promoted incumbent is only a legal constructive solver, the next useful
increment is usually:

1. add `assignment + machine_sequences` extraction from the incumbent schedule;
2. add `decode_state` and prove it reproduces a complete legal schedule;
3. add one bounded critical-block same-machine move;
4. add one bounded alternative-machine insertion move;
5. add tabu memory and candidate shortlisting.

If a previous round already added random machine reassignment hill climbing,
the next round should not add another random hill climber. Upgrade it by:

1. selecting operations from critical path/critical blocks instead of all ops;
2. replacing random insertion positions with bounded N8 and k-insertion
   candidate sets;
3. adding tabu memory, aspiration, and occasional perturbation so the search has
   both intensification and diversification;
4. keeping the emitted result equal to the best decoded incumbent, never the
   last non-improving current state.

Make only one of these structural increments in a round unless the previous
round explicitly repaired that same direction. Preserve the promoted incumbent
and rollback any candidate that fails decoding or is worse under the Core
evaluator.
