# Harness Agent Architecture

## 1. Why Harness First

Algorithm self-evolution needs a trusted engineering layer before it needs a
stronger model.  A model can propose rules and write code, but it must not be
trusted to decide whether its own candidate is correct.  The harness provides
that boundary.

The harness is the stable part of the system:

- it owns the task contract;
- it owns evaluator execution;
- it owns the experiment ledger;
- it owns final candidate status;
- it owns artifact paths and reproducibility.

The worker is the unstable part:

- it proposes heuristic ideas;
- it writes or edits candidate solvers;
- it may self-test;
- it may summarize failures;
- it never owns the final verdict.

## 2. Core Objects

| Object | Role |
| --- | --- |
| `TaskContract` | Versioned task definition extracted from documents and user requirements. |
| `InstanceSpec` | One benchmark or industrial test case. |
| `ObjectiveSpec` | A metric name, direction, priority, and required status. |
| `CommandSpec` | Solver, evaluator, and quick-test command templates. |
| `HarnessRunner` | Runs solver/evaluator commands and records results. |
| `GraphHarnessRunner` | LangGraph orchestration wrapper around the deterministic runner. |
| `ExperimentLedger` | SQLite-backed fact source for every experiment. |
| `CodingWorker` | Interface for DeepSeek/Codex/OpenCode/Pi-style proposal backends. |

## 3. Main-Agent Framework

The main agent uses LangGraph as the orchestration framework.  LangGraph is not
the optimizer; it is the state machine that makes each step explicit:

1. ingest requirement and IO documents;
2. ask a worker or template generator for a strategy profile;
3. split the profile into strategy candidates;
4. build one task contract per candidate;
5. run solver commands through the harness;
6. run the fixed evaluator;
7. compare candidate metrics;
8. write the ledger, report, and reflection;
9. append a structured hypothesis record;
10. decide whether another round should be executed.

This separation is intentional.  It keeps the evolving part of the system
replaceable while preserving deterministic evaluation and reproducibility.

The MVP agent surface is deliberately small:

- one LangGraph main agent owns orchestration, review state, experiment memory,
  and final candidate decisions;
- one coding agent backend writes or modifies code inside guarded experiment
  boundaries;
- strategy generation is a capability of the coding agent by default, and is
  split into a separate strategy agent only if context length, parallel strategy
  comparison, or audit requirements make that separation necessary.

Document parsing, domain routing, reflection, and candidate review are graph
nodes or core services, not separate default agents.  This avoids unnecessary
multi-agent coordination overhead while keeping the loop auditable.

## 4. Worker Backends

Worker backends are proposal engines, not judges.

- `DeepSeekWorker` currently generates structured strategy profiles from the
  supplied documents and previous reports.  It can also consume a context packet
  and produce a guarded code-edit proposal.  Proposal output is normalized and
  path-checked by the harness before any optional apply step.
- `OpenCodeWorker` is an adapter boundary for a future coding agent that can
  edit candidate solver code once the executable is available and guarded edit
  flow is implemented.
- Additional backends can be added if they return artifacts that the harness can
  evaluate deterministically.

The harness remains responsible for validity, metrics, best-known gap
calculation, and final reporting.  No worker result is accepted without an
evaluator run.

Code-edit workers follow a proposal-first protocol.  The default behavior is to
write `proposal.json` / `proposal.md` artifacts without touching the worktree.
If `--apply` is explicitly requested, only full-file `create_or_replace` edits
that pass allowed-path and forbidden-path checks are written into the specified
worktree.  This keeps code generation useful while preventing a worker from
silently editing evaluator artifacts, output directories, or Git metadata.

`run-worker-cycle` turns that proposal protocol into one evaluator-backed loop:
copy the allowed project surface into an isolated candidate worktree, run the
worker, optionally apply accepted edits, and then invoke the deterministic
LangGraph harness in the candidate tree.  The cycle report records both worker
status and Core evaluator metrics, keeping the proposal layer separate from the
final verdict.

`run-worker-loop` repeats that cycle with an incumbent policy.  It evaluates a
baseline first, then promotes a candidate worktree only when the Core
evaluator-backed objective key is strictly better than the incumbent.  Failed,
invalid, or non-improving candidates are rolled back by leaving the incumbent
worktree unchanged.  This is the first full loop-engineering path:
context packet, worker proposal, guarded apply, Core evaluation, reflection-ready
reporting, and promotion/rollback memory.

## 5. Hypothesis Memory

The self-evolution loop needs more than free-form reflection text.  Each
standard-agent round appends a JSONL hypothesis record with:

- the parent hypothesis;
- strategy source and solver;
- comparable score metric;
- score delta from the parent;
- evaluator summary;
- artifact paths.

This record is passed into the next round as structured feedback.  It is also a
machine-readable trail for later pruning, mutation, and operator-level
evolution.

## 6. Strategy-Candidate Evaluation

A single LLM response may contain multiple heuristic ideas.  The agent therefore
does not trust the merged response blindly.  It creates bounded strategy
candidates:

- the complete strategy profile;
- single-strategy ablations;
- deterministic profile mutations such as chain bias and machine-load bias.

Each candidate receives its own task contract and harness output directory.  The
selected candidate is the one with the best evaluator-backed score, usually
lowest average best-known gap when `Best.csv` is available.

## 7. Contract-Driven Execution

The harness should never hard-code a single metric such as makespan, production
weight, or setup count.  Metrics come from the task contract, which should be
derived from the provided requirement and IO documents.

The evaluator output is expected to contain:

```json
{
  "valid": true,
  "error_count": 0,
  "errors": [],
  "metrics": {
    "primary_score": 1.0
  }
}
```

The harness can compare candidates only after validity is known and all required
objective metrics are present.

Generated contracts carry a `review.status`.  If the status is
`draft_requires_human_confirmation`, formal `run` refuses the contract unless the
caller explicitly passes `--allow-draft` for exploration.  A reviewed contract is
created with `confirm-contract`, which records who confirmed it and when.  This
implements the rule that generated evaluators and validators must not become
formal judges until a human confirms their semantics.

Coding workers do not receive the whole repository context by default.  The main
agent writes a context packet containing the task contract hash, review status,
evaluator protocol, edit policy, bounded document snippets, knowledge cards,
previous report, and current hypothesis.  This packet is the stable boundary
between orchestration and code generation.

For standard FJSP benchmarks, the evaluator can also load a best-known CSV.
When the evaluated instance name appears in the table, the metrics include both
`best_known_makespan` and `gap_pct`.

## 8. Operator-Profile Evolution

The standard FJSP solver exposes two separate evolution surfaces:

- strategy profiles control constructive dispatch-rule weights;
- neighborhood profiles control the local-search operator family.
- run profiles bundle neighborhood choice with budget parameters such as
  portfolio size, restart count, neighbor limit, and time limit.

This distinction matters because changing only dispatch weights cannot emulate a
strong tabu search.  The current local-search solver supports `random`,
`critical-block`, and `combined` neighborhood profiles.  A standard-agent round
can cross-evaluate multiple neighborhood profiles against each generated
strategy profile, or evaluate named run profiles such as `balanced-combined` and
`deep-combined`, then select the best pair with evaluator-backed metrics instead
of relying on hidden solver constants.

The harness budget also supports `max_workers` so independent instance/seed
experiments can run concurrently.  This is important for self-evolution because
stronger tabu-search candidates, such as N8/k-insertion profiles inspired by
Xie Jin's HGTSA dissertation, need more neighborhood evaluations than a serial
round can usually afford.

The current knowledge base contains a dedicated HGTSA operator specification at
`knowledge/imported_huawei_fjsp_knowledge/operators/xiejin_hgtsa_n8_k_insertion_tabu_spec.md`.
It should be treated as the source card for future `hgtsa-lite` neighborhood
profiles: N8 critical-block moves, k-insertion machine reassignment, tabu
attributes, and approximate candidate ranking before full decoding.

## 9. Repository Separation

This project should remain independent from any concrete FJSP solver repository.
Concrete solvers are attached through command templates or worker backends.

Recommended layout:

```text
fjsp_harness_agent/
  harness_agent/
  configs/
  docs/
  examples/
  outputs/
```

The Huawei FJSP solver repository may call this harness, or this harness may call
that solver, but source trees and Git histories should stay separate.
