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
3. build a task contract;
4. run solver commands through the harness;
5. run the fixed evaluator;
6. write the ledger, report, and reflection;
7. decide whether another round should be executed.

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

## 5. Contract-Driven Execution

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

## 6. Repository Separation

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
