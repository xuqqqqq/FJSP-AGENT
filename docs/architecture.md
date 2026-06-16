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
- `OpenCodeWorker` runs `opencode run` non-interactively when an executable is
  available.  It writes prompt, command, stdout, and stderr artifacts, then
  leaves acceptance to the harness diff and evaluator stages.
- Additional backends can be added if they return artifacts that the harness can
  evaluate deterministically.

The harness remains responsible for validity, metrics, best-known gap
calculation, and final reporting.  No worker result is accepted without an
evaluator run.

Before worker evolution begins, the harness can run a contract health check.
This preflight validates referenced instance/resource paths, executes the
contract quick test, and repeats a small fixed-seed solver/evaluator probe.  The
probe is designed to detect unusable commands, missing metrics, and unstable
benchmark behavior before any coding worker spends budget on algorithm changes.
It is not an optimization result; it is an admission check for the evaluator
surface.

Before that admission check, the harness can run project intake.  This stage is
a bounded, read-only scan of the current repository: language mix, Git state,
entry points, likely solver/evaluator/benchmark files, dependency manifests,
data directories, inferred test commands, edit policy, and risk flags.  The
intake manifest gives a coding worker a grounded project map without granting it
authority to judge candidate quality.

After health evidence exists, the harness can write an intent-alignment card.
This card is the auditable translation from documents and task contract into the
optimization target: primary and secondary objectives, hard constraints,
commands, budget, benchmark source, health status, overfitting risk, blockers,
and warnings.  Formal optimization should proceed only when the card reports
that the task is ready.  Draft contracts, missing health evidence, failed
preflight checks, or invalid paths remain blockers until explicitly addressed.

Code-edit workers follow a proposal-first protocol.  The default behavior is to
write `proposal.json` / `proposal.md` artifacts without touching the worktree.
If `--apply` is explicitly requested, only full-file `create_or_replace` edits
that pass allowed-path and forbidden-path checks are written into the specified
worktree.  This keeps code generation useful while preventing a worker from
silently editing evaluator artifacts, output directories, or Git metadata.
DeepSeek proposals are normalized with a deterministic `proposal_audit` section:
the harness records whether project intake was present, whether the worker
declared that it used it, which intake files were referenced, whether accepted
edits touch core solver files, and whether they touch validator or benchmark
candidates.  These fields support diagnosis and later reflection; they do not
change the evaluator-backed promotion rule.

`run-worker-cycle` turns that proposal protocol into one evaluator-backed loop:
copy the allowed project surface into an isolated candidate worktree, run the
worker, optionally apply accepted edits, and then invoke the deterministic
LangGraph harness in the candidate tree.  The cycle report records both worker
status and Core evaluator metrics, keeping the proposal layer separate from the
final verdict.  It also records a harness-generated worktree delta and unified
patch.  These artifacts are computed from before/after file snapshots, so they
remain available even when the worker summary is incomplete or overly
optimistic.

`run-worker-loop` repeats that cycle with an incumbent policy.  It evaluates a
baseline first, then promotes a candidate worktree only when the Core
evaluator-backed objective key is strictly better than the incumbent.  Failed,
invalid, or non-improving candidates are rolled back by leaving the incumbent
worktree unchanged.  This is the first full loop-engineering path:
context packet, worker proposal, guarded apply, Core evaluation, reflection-ready
reporting, and promotion/rollback memory.

The loop does not reuse the original context packet unchanged.  Before each
candidate cycle, the main agent writes a refreshed packet for that round.  The
packet preserves the original contract, project-intake summary, document
snippets, and knowledge cards, then appends compact evaluator evidence:
baseline key, incumbent key, prior candidate summaries, and promotion/rollback
decisions.  This makes later worker proposals condition on measured outcomes
rather than on static prompt text.

When a worker produces a structured proposal artifact, the refreshed packet also
receives compact proposal diagnostics from prior rounds.  The diagnostics record
whether the proposal declared project-intake usage, which files were referenced,
whether edits targeted core solver files, whether validator or benchmark files
were touched, which quick-test commands were referenced, and any deterministic
audit warnings.  This gives the next worker round concrete reflection material
without turning proposal quality into an acceptance rule.

Proposal diversity is tracked as an audit signal.  The loop computes a stable
fingerprint from the worker proposal artifact, or from the worker status and
changed-file set when no proposal artifact exists.  Repeated fingerprints are
flagged in reports and fed into the next context packet.  They are not used as a
success or failure verdict; promotion remains evaluator-only.

Generated loop artifacts can be indexed after the fact with the evidence-index
command.  The index scans project-intake, health-check, intent-alignment, demo,
benchmark-suite, and standard worker-loop manifests, checks whether referenced
reports still exist, and
writes one JSON/Markdown table of statuses, valid experiment counts, gap
metrics, stability probes, intent-readiness flags, and worker-loop promotion
evidence.  This is intentionally read-only: it is an audit surface over existing
evaluator-backed outputs, not a new evaluator.

For standard FJSP smoke tests, `run-standard-pipeline` composes the benchmark
project intake, health check, intent alignment, benchmark suite, coding-worker
loop, and evidence-index commands into one reproducible entrypoint.  The
pipeline has no independent scoring authority.  If the admission stages fail,
the pipeline skips benchmark-suite and worker-loop execution; if they pass, it
succeeds only when the underlying evaluator-backed stages produce complete
manifests and referenced artifacts.  The generated project-intake manifest is
also passed into the standard worker-loop context packet, so code-generation
backends receive a bounded repository map before proposing edits.

The same pipeline writes a compact memory artifact for the next orchestration
turn.  The memory file condenses admission status, benchmark best-known gap
signals, worker-loop promotion/rollback outcomes, per-round proposal
diagnostics, evidence-index completeness, and deterministic next-step
recommendations.  It is a prompt handoff and reporting aid, not a scoring layer:
all acceptance decisions remain tied to the source manifests and fixed
evaluator metrics.

The handoff is also consumable by the next run.  `build-context-packet`,
`run-standard-worker-loop`, and `run-standard-pipeline` accept a previous memory
file and embed a compact `previous_pipeline_memory` object in the worker context
packet.  This closes the loop between one evaluator-backed pipeline run and the
next proposal round while keeping the original reports and manifests auditable.
When `run-standard-pipeline --loop-rounds N` is used, the same handoff is chained
automatically: each `iteration_xxx` directory is a complete pipeline run, and
iteration `k + 1` receives iteration `k`'s `standard_pipeline_memory.json` in
its worker context packet.  The top-level loop manifest is a navigation and
audit summary only; it does not replace the per-iteration evaluator evidence.
The top-level loop also writes a next-action brief.  This file is a small
controller handoff for a future planner or coding backend: it points to the
memory artifact that should be supplied as `--previous-memory`, summarizes gap
and promotion trends, lists focus areas such as duplicate proposals or missing
benchmark improvement, and drafts a next-worker hypothesis.  It deliberately
stays outside acceptance logic so the loop can become more autonomous without
weakening evaluator authority.
The same focus extraction is used inside a multi-round pipeline run.  After
iteration `k`, the controller reads iteration `k`'s memory artifact and derives
iteration `k + 1`'s `worker_hypothesis` from evaluator-grounded
recommendations.  This is the first closed control loop: evidence changes the
next prompt while evaluation, promotion, and admission gates remain unchanged.
`--no-adapt-worker-hypothesis` disables that control link for ablation studies.

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

The standard agent now materializes that trail as a hypothesis graph summary.
Each evaluated hypothesis receives an advisory decision: `promote` for elite
evaluator-backed scores, `prune` for missing or clearly worse evidence, and
`mutate` for useful ancestors or non-elite comparable records.  The graph is
written as JSON and Markdown, then injected into the next strategy-generation
context.  These decisions guide exploration; they do not accept candidates
without evaluator confirmation.

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

Draft contract generation is source-grounded rather than authoritative.  The
builder reads Markdown documents, records source references, extracts obvious
feature and metric hints, and now emits a section-level document schema with
headings, line ranges, inferred roles, and per-section hints.  That schema gives
later worker prompts a structured map of where objectives, constraints,
input-output definitions, algorithm guidance, and acceptance language appeared.
It does not bypass human confirmation: generated contracts remain drafts until
the evaluator semantics and objective metrics are reviewed.

Context packets include a compact form of that contract-review evidence.  The
worker sees review status, uncertain fields, extracted feature/metric hints, and
the bounded section schema alongside document snippets.  This keeps the
document-driven loop grounded in the source Markdown while avoiding long prompt
payloads and preserving the rule that generated evaluator semantics require
review before formal optimization.

For long requirement documents, the context packet also derives
`role_prioritized_sections` from the same schema.  This list ranks sections by
their inferred role, so objectives, hard constraints, input/output semantics,
acceptance criteria, and algorithm guidance are surfaced before general prose.
It is a prompt-compression aid only: the full compact schema remains available,
and evaluator acceptance remains unchanged.

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

For multi-objective contracts, the runner reports both the lexicographic best
candidate and a Pareto frontier over complete candidate aggregates.  Objective
keys are normalized so larger is always better, including minimized metrics
after sign conversion.  Reports also include status/error counts so evaluator
schema failures, missing metrics, and runtime failures remain visible during
self-evolution.

Generated contracts carry a `review.status`.  If the status is
`draft_requires_human_confirmation`, formal `run` refuses the contract unless the
caller explicitly passes `--allow-draft` for exploration.  A reviewed contract is
created with `confirm-contract`, which records who confirmed it and when.  This
implements the rule that generated evaluators and validators must not become
formal judges until a human confirms their semantics.

The draft-contract builder performs source-grounded extraction before that
confirmation step.  It records problem-feature hints, inferred metric hints,
command-template placeholder checks, document statistics, and a confirmation
checklist under `review`.  These fields help the main agent and reviewer see how
the requirement/IO documents were translated, but they remain non-authoritative
until the contract is confirmed.

Alongside the JSON draft, the builder also writes a deterministic
`*.review.md` card generated from the same payload.  This card is the
human-readable review surface for objectives, command-template checks,
feature/metric hints, Markdown section roles, line ranges, and confirmation
tasks.  It is deliberately derived from the JSON rather than maintained as a
second source of truth.

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
