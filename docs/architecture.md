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

## 4. Worker Backends

Worker backends are proposal engines, not judges.

- `DeepSeekWorker` currently generates structured strategy profiles from the
  supplied documents and previous reports.
- `OpenCodeWorker` is an adapter boundary for a future coding agent that can
  edit candidate solver code once the executable is available and guarded edit
  flow is implemented.
- Additional backends can be added if they return artifacts that the harness can
  evaluate deterministically.

The harness remains responsible for validity, metrics, best-known gap
calculation, and final reporting.  No worker result is accepted without an
evaluator run.

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
