---
name: fjsp-machine-availability-adapter-worker
description: 为已选 FJSP 方法族适配固定机器维修、downtime 与不可用窗口，并实现日历感知解码、搜索和验收。仅在 runtime contract 激活 machine_availability 或 machine_calendar 时使用。
---

# Machine-Availability FJSP Adapter

## Contract

- This Skill is a variant adapter, not the formal optimization family. Preserve the assigned constructive, coupled-local, population-memetic, or exact-hybrid family.
- Parse the IO tail `K + K*(machine_id,start,end)` as half-open intervals `[start,end)`; overlapping or touching intervals may be merged only with identical union semantics.
- Operations are non-preemptive and must lie wholly outside every interval on their selected machine.
- Preserve the fixed CLI and evaluator.

## Implementation

1. Normalize each machine calendar once into sorted merged intervals and share it across construction, decoding, neighborhoods, population operators, exact repair, and self-checks.
2. Earliest placement must scan scheduled operations and maintenance windows until a complete processing gap fits. Append-only `machine_ready` logic is not calendar-aware.
3. Constructive paths score complete calendar gaps. Coupled local search re-decodes reassignment/insertion/swap moves. Population or memetic paths re-decode crossover, mutation, and local improvement. Never compare a candidate before calendar validation.
4. CP-SAT paths add fixed maintenance intervals to each machine's `NoOverlap`; a heuristic fallback must still enforce the same calendar contract.
5. Before reporting success, self-check every operation against every original downtime window on its selected machine. Equality at `end == downtime_start` and `start == downtime_end` is legal; spanning or intersecting a window is illegal.
6. Report activation evidence for the tail parser, normalized calendars, every reachable candidate path, a machine with multiple windows, both legal boundaries, and one rejected intersection.

Read the two machine-availability knowledge cards and the assigned Method Package before editing.
