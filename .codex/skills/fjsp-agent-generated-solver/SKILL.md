---
name: fjsp-agent-generated-solver
description: Use when generating, reviewing, or evolving a standalone agent-written FJSP/FJSP-SDST solver from requirement and IO documents, especially when the backend must remain algorithm-agnostic and solver code must be produced by the coding agent rather than supplied by platform orchestration.
---

# FJSP Agent-Generated Solver

Use this skill to keep standalone solver generation agent-owned while giving it
strong legality and search-quality guardrails.

## Workflow

1. Read the active requirement document, IO contract, evaluator protocol, and
   instance diagnostics before choosing an algorithm.
2. Identify the active variant features: alternative machines, sequence-
   dependent setup, time lags, no-wait, calendars, batching, transport, release
   dates, due dates, or objective changes.
3. Read `references/solver_contract.md` for the invariant contract.
4. For FJSP-SDST agent-generated solver work, read
   `knowledge/papers/awls_sdst_agent_generated_transfer_notes.md` when it is
   present in the project. Treat it as AWLS-derived method transfer, not source
   code to copy.
5. For FJSP-SDST or any variant with setup-aware sequencing, read
   `references/decoder_neighborhood.md`.
6. Propose one natural-language rule/operator hypothesis before writing code.
7. Generate solver code from the IO contract. Do not import backend solver
   internals, evaluator code, or previous solution files.
8. Self-check standalone CLI (`--input`, `--output`, `--seed`), active IO
   parsing, declared output schema, processing-time equality, full operation
   coverage, machine eligibility, precedence, non-overlap, variant constraints,
   incumbent preservation, and runtime before claiming a candidate is ready for
   Core evaluation.
9. When loop feedback contains `agent_generated_quality_memory`, repair its
   recurring parser, representation, constructor, decoder, variant-handling, or
   self-check gaps before proposing a new objective-improvement operator.

## Backend Boundary

- Treat this skill as method guidance, not a solver implementation.
- Do not ask backend orchestration to provide decoder or neighborhood code.
- Do not hardcode FJSP-SDST algorithms into generic pipeline, evaluator,
  parser, promotion, or web code.
- Put reusable algorithmic knowledge in this skill, knowledge cards, domain
  packs, slot manifests, or worker context.
- Promotion remains owned by the fixed Core evaluator.

## Success Standard

A useful generated solver must be both legal and evolvable:

- It must produce a complete schedule under the declared IO schema.
- It must be runnable as a standalone script with the solver command interface
  in the active evaluator protocol.
- It must preserve one operation identity representation through parsing,
  construction, local search, decoding, and output.
- It must check that every output interval has `end - start` equal to the
  selected machine's processing time.
- It must keep every move bounded by runtime and legality checks.
- It must keep the incumbent schedule if a neighborhood fails, times out, or
  cannot decode a complete candidate.
- It must explain which prior promoted mechanism it preserves and which single
  rule/operator it changes.

## Experience Memory Use

Run-local experience memory is feedback, not a solver template.  Use it to
decide which structural gaps to repair or preserve:

- Repeated `active_io_parser` or `operation_level_ready_list_constructor` gaps
  mean the next proposal should recover parser and constructor structure before
  local search.
- Repeated decoder, coverage, eligibility, or incumbent-preservation gaps mean
  every neighborhood candidate must be decoded and checked before makespan
  comparison.
- Repeated self-check gaps mean `solver_contract_self_check` must cite concrete
  code evidence for each expected capability.
- Recovered quality gaps should be preserved in later rounds unless evaluator
  feedback identifies that mechanism as the failure source.
