---
name: fjsp-priority-adapter-worker
description: 为已选 FJSP 方法族适配软工件优先级和严格词典序双目标。
---

# Job-Priority FJSP Adapter

## Contract

- Parse the standard FJSP body completely, then exactly `K` strictly ascending, unique, 0-based priority job IDs where `K = ceil(job_count / 4)`.
- Priority changes objective ranking only. It does not add precedence, release-time, due-date, or machine-capacity constraints.
- Compare complete solutions by `(makespan, priority_completion_time)` lexicographically. Never trade a worse makespan for an earlier priority completion time.
- Recompute both objectives from every fully decoded candidate before acceptance; do not trust cached deltas or solver-declared metrics.
- Preserve the fixed CLI and `standard_fjsp_schedule_v1`; do not edit parser or evaluator Core.

## Search Adaptation

1. Construction may rank ready priority operations earlier, but must retain multiple priority pressures and ordinary makespan-oriented starts.
2. Local search should include critical/near-critical operations of priority jobs, their blocking machine arcs, and assignment/insertion moves that can reduce their final completion without worsening makespan.
3. Population or memetic search should rank individuals lexicographically and retain structural diversity; a weighted sum is not an equivalent replacement.
4. CP-SAT should first minimize makespan. After obtaining the protected makespan value, constrain it and minimize the maximum completion of priority jobs. A scalar objective is acceptable only with a proven coefficient larger than every possible secondary range.
5. Report activation evidence: parsed priority IDs, both recomputed objectives, and at least one equal-makespan comparison where priority completion changes.
6. Construct the output JSON payload only after the final incumbent acceptance decision. If an exact or search phase replaces `schedule`, refresh `schedule`, `makespan`, and `priority_completion_time` together before serialization; diagnostics marked `accepted=true` must match the fixed evaluator's metrics for that serialized schedule.

Read the priority semantics and search cards plus the assigned Method Package before editing.
