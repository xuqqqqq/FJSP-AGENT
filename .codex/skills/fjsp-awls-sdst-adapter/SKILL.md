---
name: fjsp-awls-sdst-adapter
description: Use when adapting or evolving AWLS-based Flexible Job Shop Scheduling solvers for FJSP-SDST with sequence-dependent setup times, especially when selecting a complete Method Package, writing Worker Assignments, or validating setup-aware timing, neighborhoods, tabu, and adaptive scoring under fixed IO/evaluator contracts.
---

# FJSP AWLS-SDST Adapter

Use this skill to keep AWLS-SDST work agent-first and evaluator-backed.

## Workflow

1. Read the current task docs and the fixed evaluator contract.
2. Read `references/awls_sdst_adaptation.md` before proposing AWLS changes.
3. For benchmark or promotion work, read
   `knowledge/benchmarks/fjsp_benchmark_scope.md`. Read
   `knowledge/capabilities/fjsp_agent_current_capability_20260704.md` only when
   the task explicitly asks for a dated capability audit.
4. For new variant/domain-pack/RAG work, read
   `knowledge/principles/fjsp_variant_domain_pack_rag.md`.
5. If paper context is needed, read `references/paper_notes.md`; keep only compact claims in the worker prompt.
6. Require the coding worker to propose a natural-language rule/operator hypothesis before code.
7. Restrict code edits to the current Worker Assignment target; do not rewrite parser, evaluator, solution schema, or benchmark semantics.
8. Promote only by Core evaluator results. Treat worker self-evaluation as diagnostic text.

## Required Constraints

- Keep parser and evaluator behavior frozen. Standalone generated solvers must implement their own IO-derived parser and must not import `harness_agent`; platform method assets may reuse platform parsers only for validation.
- The first AWLS-SDST milestone is legality: AWLS internal `update_time`, R/Q tails, and emitted records must respect setup gaps.
- After legality, evolve N7/NK move evaluation and `zi` scoring to account for setup-aware head/tail timing.
- Keep standard FJSP behavior unchanged when `instance.has_sequence_dependent_setup` is false.
- Use one small active-task smoke before broader benchmark work.
- Report LB and UB/BKS with separate gap-to-LB and gap-to-UB diagnostics; never use LB/UB as solver inputs.
- Keep variant-specific algorithm knowledge in domain packs, knowledge cards, Skills, and Method Packages; do not hardcode it in generic backend orchestration.

## Suggested Stages

1. Setup-aware graph propagation and record output.
2. Setup-aware same-machine and change-machine move evaluation.
3. Adaptive perturbation inputs and update policy.
4. Search control, restart diversity, and time budgets.

## Validation

Always include:

- Standard FJSP smoke to prove backward compatibility.
- One small SDST smoke derived from the active task to prove evaluator legality.
- Broader claims require structurally diverse instances; do not route or validate by hardcoded instance names.
- Fixed evaluator metrics with best-known CSV when available.
- Rollback if any candidate changes parser/evaluator semantics or returns invalid schedules.
