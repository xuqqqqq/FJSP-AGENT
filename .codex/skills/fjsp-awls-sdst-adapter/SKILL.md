---
name: fjsp-awls-sdst-adapter
description: Use when adapting or evolving AWLS-based Flexible Job Shop Scheduling solvers for FJSP-SDST with sequence-dependent setup times, especially when creating code-slot proposals, writing agent prompts, or validating AWLS N7/NK/zi changes under fixed parser/evaluator contracts.
---

# FJSP AWLS-SDST Adapter

Use this skill to keep AWLS-SDST work agent-first and evaluator-backed.

## Workflow

1. Read the current task docs and the fixed evaluator contract.
2. Read `references/awls_sdst_adaptation.md` before proposing code slots or AWLS changes.
3. For benchmark, promotion, or RAG-context work, read project cards
   `knowledge/papers/fjsp_agent_current_capability_20260704.md` and
   `knowledge/benchmarks/fjsp_benchmark_scope.md` before choosing instances.
4. For new variant/domain-pack/RAG work, read
   `knowledge/principles/fjsp_variant_domain_pack_rag.md`.
5. If paper context is needed, read `references/paper_notes.md`; keep only compact claims in the worker prompt.
6. Require the coding worker to propose a natural-language rule/operator hypothesis before code.
7. Restrict code edits to user-confirmed slots; do not rewrite parser, evaluator, solution schema, or benchmark semantics.
8. Promote only by Core evaluator results. Treat worker self-evaluation as diagnostic text.

## Required Constraints

- Reuse `harness_agent.domains.io.parse_standard_fjsp` and `setup_time_between` when validating the method asset; do not create a parallel SDST parser inside the platform.
- The first AWLS-SDST milestone is legality: AWLS internal `update_time`, R/Q tails, and emitted records must respect setup gaps.
- After legality, evolve N7/NK move evaluation and `zi` scoring to account for setup-aware head/tail timing.
- Keep standard FJSP behavior unchanged when `instance.has_sequence_dependent_setup` is false.
- Use small smoke runs before broader HUdata benchmarks.
- Report LB and UB/BKS with separate gap-to-LB and gap-to-UB diagnostics; never use LB/UB as solver inputs.
- Keep variant-specific algorithm knowledge in domain packs, knowledge cards, skills, and selected slots; do not hardcode it in generic backend orchestration.

## Suggested Stages

1. `awls_sdst_time_propagation`: setup-aware AWLS graph time update and record output.
2. `awls_sdst_move_evaluation`: setup-aware same-machine and change-machine approximate evaluation.
3. `awls_sdst_zi_policy`: setup-aware adaptive perturbation inputs and formula/slot evolution.
4. `awls_sdst_portfolio`: profile selection, restart mix, and time budgets.

## Validation

Always include:

- Standard FJSP smoke to prove backward compatibility.
- SDST smoke such as `oddla20.txt` to prove evaluator legality.
- SDST quality probes should include hard HUdata cases `oddla11`--`oddla15`, not only `oddla20`.
- Standard FJSP quality probes should cover BA/BR/DP/HU and include at least one BA and one DP case before claiming broad improvement.
- Fixed evaluator metrics with best-known CSV when available.
- Rollback if any candidate changes parser/evaluator semantics or returns invalid schedules.
