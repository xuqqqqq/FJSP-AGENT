---
name: fjsp-reentrant-adapter-worker
description: 为已选 FJSP 方法族适配单连续回路尾部、完整工序展开与可重入瓶颈搜索。仅在 runtime contract 激活 reentrant_route 或 loop_expansion 时使用。
---

# Re-entrant FJSP Adapter

## Contract

- This Skill adapts the assigned constructive, coupled-local, population-memetic, or exact-hybrid family. It does not replace that family.
- Parse the standard FJSP body completely, then consume exactly `job_count` triples `(loop_start, loop_end, repeat)`.
- Validate `0 < loop_start <= loop_end < original_op_count - 1` and `repeat >= 2` for every job. Reject missing, malformed, or trailing tokens.
- Expand each route as `pre + loop_body * repeat + post`. Assign continuous 0-based `op_id` values to the expanded route; each pass may choose its machine independently.
- The fixed evaluator expects exactly one schedule record for every expanded `(job_id, op_id)`. Never schedule only the original route.
- Do not invent batching, probabilistic rework, release control, arbitrary route graphs, or pass-coupling constraints. They are outside this IO contract.

## Search Adaptation

1. Construction should use the expanded route and account for repeated-pass load. Keep complementary starts: earliest-gap/load balance, critical remaining work, and a bounded re-entry pressure such as time or operations until the next visit to a heavily loaded machine.
2. Coupled local search should recompute the critical path on the expanded graph. Prioritize critical blocks containing repeated-body operations, alternative-machine reassignment, and sequence insertion/swap followed by complete decode.
3. Population or memetic search may preserve job-order feasibility with operation-based encodings, but crossover and mutation must operate on expanded identities. Retain structural diversity rather than cloning one repeated-pass pattern.
4. CP-SAT is appropriate for small or low-flexibility expanded instances. For larger cases, use it as a bounded trust-region repair around an incumbent or on selected critical operations; report actual model size, status, time, and whether the extracted schedule passed the fixed evaluator.
5. Do not force all passes of one source operation onto the same machine. That removes flexibility not present in the contract.
6. Report activation evidence: consumed tail length, original and expanded operation counts, loop triples, continuous expanded identities, complete coverage, and at least one search mechanism that treats repeated-body pressure explicitly.

Read both re-entrant knowledge cards and the assigned Method Package before editing.
