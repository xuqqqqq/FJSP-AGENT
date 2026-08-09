---
name: fjsp-release-time-adapter-worker
description: 为已选 FJSP 方法族适配静态 job release time 与 machine initial available time。
---

# Release-Time FJSP Adapter

## Contract

- Parse exactly two rows of width `max(job_count, machine_count)`; validate `-1` padding.
- The first operation of job `j` cannot start before `r_j`.
- Every operation on machine `m` cannot start before `a_m`.
- Preserve the fixed CLI and `standard_fjsp_schedule_v1`; do not edit parser/evaluator Core.

## Implementation

1. Seed job readiness with `r_j` and machine readiness with `a_m`.
2. Apply both lower bounds in every full decode, gap insertion, move evaluation, and incumbent verification.
3. In local or population search, move representations may omit times, but every accepted candidate must be fully re-decoded.
4. For CP-SAT, add `start[j,0] >= r_j` and each selected machine alternative's start lower bound.
5. Report activation evidence using nonzero release values and one rejected violating schedule.

Read the two release-time knowledge cards and the assigned Method Package before editing.
