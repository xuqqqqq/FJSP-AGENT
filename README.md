# FJSP Harness Agent

FJSP Harness Agent is an independent engineering project for running algorithm
self-evolution experiments under fixed evaluators.

The main agent orchestration layer is implemented with LangGraph.  The harness
still treats the evaluator as the source of truth; LangGraph only coordinates
state transitions such as preparing runs, executing candidate solvers, invoking
evaluators, writing the ledger, and summarizing results.

It does not solve FJSP by itself.  Its job is to provide the trusted execution
shell around candidate solvers:

1. read a task contract generated from requirement, IO, and metric documents;
2. create isolated experiment artifacts;
3. run candidate solver commands;
4. run fixed evaluator / validator commands;
5. parse metrics;
6. write an auditable experiment ledger;
7. summarize the best candidate and failure reasons.

The project is intentionally separated from the existing Huawei FJSP solver
repository so that the harness layer can become its own GitHub project.

## Quick Start

```powershell
cd F:\huawei_fjsp_llm\fjsp_harness_agent
python -m harness_agent.cli validate-contract --contract configs\task_contract.example.json
python -m harness_agent.cli run --contract configs\task_contract.example.json --output-dir outputs\demo
```

Expected outputs:

- `outputs/demo/harness.sqlite3`
- `outputs/demo/experiments/...`
- `outputs/demo/report.md`

Run the lightweight regression suite with:

```powershell
python -m unittest discover -s tests -v
```

## Contract Health Check

Before running a self-evolution loop, use `health-check` to verify that the task
contract is runnable.  The command validates referenced paths, executes the
contract quick test, and repeats a small solver/evaluator probe with the same
instance and seed to detect unstable benchmark behavior.

```powershell
python -m harness_agent.cli health-check `
  --contract configs\standard_fjsp_tiny.example.json `
  --output-dir outputs\standard_fjsp_health `
  --repeats 2 `
  --max-instances 1 `
  --max-seeds 1
```

Expected outputs:

- `outputs/standard_fjsp_health/health_check_manifest.json`
- `outputs/standard_fjsp_health/health_check_report.md`
- `outputs/standard_fjsp_health/stability_probe/report.md`

The health check is a preflight gate, not an optimization score.  It proves that
the evaluator path is usable and repeatable enough to start a loop; later
benchmark and worker-loop stages still own candidate quality decisions.

## Intent Alignment Summary

After health-check evidence is available, generate an intent-alignment card.
This card is the reviewable translation from documents and task contract into
optimization intent: objectives, hard constraints, commands, benchmark source,
budget, health status, overfitting risk, blockers, and warnings.

```powershell
python -m harness_agent.cli intent-alignment `
  --contract configs\standard_fjsp_tiny.example.json `
  --health-manifest outputs\standard_fjsp_health\health_check_manifest.json `
  --output-dir outputs\standard_fjsp_intent
```

Expected outputs:

- `outputs/standard_fjsp_intent/intent_alignment_manifest.json`
- `outputs/standard_fjsp_intent/intent_alignment_report.md`

Formal optimization should start only when the card reports
`ready_for_optimization=true`.  Draft contracts, missing health evidence, failed
health checks, or invalid task references are reported as blockers instead of
being silently ignored.

## Standard FJSP End-To-End Demo

`run-demo` is the compact closed-loop entry point for the current standard-FJSP
prototype.  It starts from documents and benchmark instances, generates strategy
candidates, builds evaluator-backed contracts, runs the fixed solver/evaluator
path through the LangGraph harness, records hypotheses, and writes a demo
manifest.

```powershell
python -m harness_agent.cli run-demo `
  --doc README.md `
  --instance-dir examples `
  --pattern standard_fjsp_tiny.fjs `
  --output-dir outputs\standard_fjsp_demo `
  --max-rounds 2 `
  --seeds 0 `
  --solver portfolio `
  --portfolio-size 8 `
  --strategy-candidates 2 `
  --profile-mode template
```

For benchmark batches with known upper bounds, pass a CSV with an instance-name
column and a best-known makespan column.  The standard evaluator accepts common
headers such as `instance,best`, `name,best_known`, or `file,ub`:

```powershell
python -m harness_agent.cli run-demo `
  --doc README.md `
  --instance-dir C:\Users\ASUS\Downloads\FJSP-Instance-main\FJSP-Instance-main\instance `
  --pattern "fjsp.barnes.mt10*.txt" `
  --best-known-csv path\to\Best.csv `
  --output-dir outputs\barnes_demo `
  --max-instances 3 `
  --max-rounds 2 `
  --seeds 0 `
  --solver local-search `
  --strategy-candidates 2 `
  --profile-mode template
```

Expected outputs:

- `outputs/standard_fjsp_demo/demo_manifest.json`
- `outputs/standard_fjsp_demo/demo_report.md`
- `outputs/standard_fjsp_demo/standard_agent/agent_report.md`
- `outputs/standard_fjsp_demo/standard_agent/hypotheses.jsonl`
- `outputs/standard_fjsp_demo/standard_agent/hypothesis_graph.md`

The demo report is evidence that the loop executed; the evaluator output inside
the generated harness reports remains the source of truth for solver quality.
When `--best-known-csv` is provided and instance names match, the demo manifest
adds `benchmark_summary.gap_metrics`, including metrics such as `avg_gap_pct`.

## Standard FJSP Benchmark Suite

For repeated benchmark evidence, put one or more standard-FJSP suites in a JSON
configuration and run them together:

```powershell
python -m harness_agent.cli run-benchmark-suite `
  --config configs\standard_fjsp_suite.example.json `
  --output-dir outputs\standard_fjsp_suite_demo
```

The suite config supports shared `defaults` plus per-suite overrides:

```json
{
  "defaults": {
    "docs": ["../README.md"],
    "max_rounds": 1,
    "seeds": [0],
    "solver": "portfolio",
    "profile_mode": "template"
  },
  "suites": [
    {
      "name": "tiny-standard-fjsp",
      "instance_dir": "../examples",
      "pattern": "standard_fjsp_tiny.fjs",
      "best_known_csv": "standard_fjsp_tiny_best.csv",
      "max_instances": 1
    }
  ]
}
```

Expected outputs:

- `outputs/standard_fjsp_suite_demo/suite_manifest.json`
- `outputs/standard_fjsp_suite_demo/suite_report.md`
- one subdirectory under `outputs/standard_fjsp_suite_demo/suites/` per suite

The suite report aggregates evaluator-backed valid/failed experiment counts,
best-known gap availability, and per-suite makespan/gap metrics.  It is the
recommended smoke path before adding larger Barnes, Brandimarte, or
Dauzère-Pérès benchmark batches.

## Standard FJSP Coding-Worker Loop

`run-demo` and `run-benchmark-suite` evolve strategy profiles and parameters.
`run-standard-worker-loop` exercises the code-evolution lane: it builds a
standard-FJSP contract, packages a context packet for a coding worker, evaluates
a baseline worktree, then runs candidate worker cycles with evaluator-backed
promotion or rollback.

Safe smoke run with the no-op worker:

```powershell
python -m harness_agent.cli run-standard-worker-loop `
  --worker null `
  --doc README.md `
  --instance-dir examples `
  --pattern standard_fjsp_tiny.fjs `
  --best-known-csv configs\standard_fjsp_tiny_best.csv `
  --output-dir outputs\standard_worker_loop_demo `
  --iterations 1 `
  --seeds 0 `
  --solver portfolio `
  --portfolio-size 4
```

Expected outputs:

- `outputs/standard_worker_loop_demo/standard_worker_contract.json`
- `outputs/standard_worker_loop_demo/context_packet.json`
- `outputs/standard_worker_loop_demo/standard_worker_loop_manifest.json`
- `outputs/standard_worker_loop_demo/standard_worker_loop_report.md`
- `outputs/standard_worker_loop_demo/worker_loop/loop_report.md`

When `--worker deepseek` or `--worker opencode` is used, worker proposals may
modify files inside isolated candidate worktrees.  A round is promoted only if
the fixed evaluator reports a strictly better objective key than the incumbent;
otherwise the candidate is rolled back.

## Project Intake

Before a coding worker edits solver code, the harness can write a bounded
read-only project map.  The map records language mix, Git state, likely entry
files, core algorithm files, dependency files, benchmark/evaluator candidates,
test commands, data directories, output-format hints, edit policy, and risk
flags.

```powershell
python -m harness_agent.cli project-intake `
  --project-root . `
  --contract configs\standard_fjsp_tiny.example.json `
  --output-dir outputs\standard_project_intake
```

Expected outputs:

- `outputs/standard_project_intake/project_intake_manifest.json`
- `outputs/standard_project_intake/project_intake_report.md`

Project intake does not run solvers or evaluators.  It is context evidence for
later coding-worker prompts and audit reports.

## Standard FJSP Full Pipeline

`run-standard-pipeline` is the current one-command smoke path for the standard
FJSP loop-engineering flow.  It first writes the project-intake map, then runs
the configured benchmark suite, runs the coding-worker loop, and builds a
single evidence index over all stages.  When a health contract is provided, the
pipeline also runs health-check and intent-alignment before optimization.  If
either admission stage blocks, suite and worker-loop execution are skipped
instead of spending optimization budget after a failed admission gate.

```powershell
python -m harness_agent.cli run-standard-pipeline `
  --suite-config configs\standard_fjsp_suite.example.json `
  --output-dir outputs\standard_pipeline_demo `
  --health-contract configs\standard_fjsp_tiny.example.json `
  --health-repeats 2 `
  --worker null `
  --worker-doc README.md `
  --worker-instance-dir examples `
  --worker-pattern standard_fjsp_tiny.fjs `
  --worker-best-known-csv configs\standard_fjsp_tiny_best.csv `
  --worker-iterations 1 `
  --worker-seeds 0 `
  --worker-timeout-seconds 30 `
  --worker-max-runtime-seconds 30 `
  --worker-max-steps 1 `
  --worker-solver portfolio `
  --worker-portfolio-size 4
```

Expected outputs:

- `outputs/standard_pipeline_demo/standard_pipeline_manifest.json`
- `outputs/standard_pipeline_demo/standard_pipeline_report.md`
- `outputs/standard_pipeline_demo/standard_pipeline_memory.json`
- `outputs/standard_pipeline_demo/standard_pipeline_memory.md`
- `outputs/standard_pipeline_demo/project_intake/project_intake_report.md`
- `outputs/standard_pipeline_demo/health_check/health_check_report.md`
- `outputs/standard_pipeline_demo/intent_alignment/intent_alignment_report.md`
- `outputs/standard_pipeline_demo/benchmark_suite/suite_report.md`
- `outputs/standard_pipeline_demo/standard_worker_loop/standard_worker_loop_report.md`
- `outputs/standard_pipeline_demo/evidence_index/evidence_index.md`

The pipeline is intentionally only orchestration glue.  It does not override
health-check status, intent-readiness decisions, suite metrics, worker-loop
promotion decisions, or evidence-index checks; those remain owned by the
evaluator-backed components that produced the referenced manifests.
When project intake is enabled, the standard worker-loop context packet receives
the generated intake manifest summary and report snippet automatically, so the
coding backend sees the repository map before proposing code changes.
The `standard_pipeline_memory.*` files are compact handoff artifacts for the
next loop iteration.  They combine admission status, benchmark gap signal,
worker-loop promotion/rollback evidence, proposal diagnostics, and deterministic
next-step recommendations without replacing the underlying evaluator manifests.
On a later run, pass the prior JSON back with `--previous-memory
outputs\standard_pipeline_demo\standard_pipeline_memory.json`; the standard
worker context packet will expose it as `previous_pipeline_memory` before the
coding backend proposes another solver change.
For an automatic multi-round loop, keep the same command and add
`--loop-rounds 2` or larger.  The command writes
`standard_pipeline_loop_manifest.json` and `standard_pipeline_loop_report.md`
under the requested output directory, while each full iteration is stored under
`iteration_000`, `iteration_001`, and so on.  Every iteration after the first
receives the previous iteration's `standard_pipeline_memory.json` automatically.
The loop also writes `standard_pipeline_next_action_brief.json/md`.  This brief
is the controller handoff for the next run: it records the final memory path,
benchmark trend, worker promotion summary, focus areas, and a suggested
next-worker hypothesis.  It is not a success verdict; fixed evaluator and
admission manifests still own all acceptance decisions.

## Evidence Index

After running demos, benchmark suites, or coding-worker loops, build a single
evidence index over the generated manifests:

```powershell
python -m harness_agent.cli build-evidence-index `
  --input-dir outputs\standard_fjsp_demo `
  --input-dir outputs\standard_fjsp_suite_demo `
  --input-dir outputs\standard_worker_loop_demo `
  --output-dir outputs\evidence_index
```

Expected outputs:

- `outputs/evidence_index/evidence_index.json`
- `outputs/evidence_index/evidence_index.md`

The index does not rerun solvers.  It scans `project_intake_manifest.json`,
`health_check_manifest.json`, `intent_alignment_manifest.json`,
`demo_manifest.json`, `suite_manifest.json`, and
`standard_worker_loop_manifest.json`, then summarizes status counts,
valid/total experiments, best-known gap metrics, coding-worker improvement
flags, project risk flags, health-check stability, intent-readiness flags, and
missing referenced artifacts.

## Document To Draft Contract

AlgoForge starts from requirement, IO, and metric documents rather than from a
hand-written solver script.  The current first step is `draft-contract`: it reads
source documents and CLI hints, then writes a review-required task contract.

The generated contract is intentionally marked as a draft.  If the evaluator or
validator was generated or inferred, a human must confirm the objective,
constraint, command, and metric semantics before the harness treats it as the
formal source of truth.

```powershell
python -m harness_agent.cli draft-contract `
  --doc docs\architecture.md `
  --instance examples\dummy_instance.json `
  --output outputs\draft_contract.json `
  --task-id draft_dummy `
  --objective primary_score:maximize:1 `
  --solver-cmd "python examples/dummy_solver.py --input {instance} --output {solution} --seed {seed}" `
  --evaluator-cmd "python examples/dummy_evaluator.py --instance {instance} --solution {solution} --metrics {metrics}" `
  --quick-test "python -m py_compile examples/dummy_solver.py examples/dummy_evaluator.py"
```

This command records `review.status = draft_requires_human_confirmation` and
stores source references, uncertain fields, extracted problem-feature hints,
metric hints, command-template placeholder checks, and a confirmation checklist
in the contract JSON.  These fields are evidence for review; they are not a
formal evaluator contract until confirmed.  After review, create a confirmed
copy before treating the evaluator as formal:

```powershell
python -m harness_agent.cli confirm-contract `
  --contract outputs\draft_contract.json `
  --output outputs\confirmed_contract.json `
  --confirmed-by reviewer-name `
  --note "Objectives, evaluator command, and validity semantics reviewed."
```

`run` refuses unconfirmed draft contracts by default.  Use `--allow-draft` only
for exploratory runs that must not be reported as formal evidence.

## Context Packet For Coding Agents

Before a coding backend edits solver code, the main agent packages a bounded
context packet.  This is the auditable handoff from LangGraph orchestration to a
CodingWorker such as OpenCode + DeepSeek:

```powershell
python -m harness_agent.cli build-context-packet `
  --contract outputs\confirmed_contract.json `
  --doc docs\architecture.md `
  --knowledge-card knowledge\principles\harness_agent_design.md `
  --project-intake-manifest outputs\standard_project_intake\project_intake_manifest.json `
  --previous-report outputs\demo\report.md `
  --hypothesis "Try a conservative solver improvement under confirmed evaluator semantics." `
  --output outputs\context_packet.json
```

The packet records the contract hash, review status, evaluator protocol, edit
policy, project-intake summary, bounded document snippets, knowledge cards,
previous report, and worker instructions.  A worker may self-test against it,
but AlgoForge Core still owns the final evaluator run and success verdict.

## Coding Worker Run

`run-worker` is the guarded execution surface for a coding backend.  It consumes
a context packet and writes worker artifacts.  The safe smoke backend is
`null`; the DeepSeek backend can generate a structured code-edit proposal when
`DEEPSEEK_API_KEY` is configured.

```powershell
python -m harness_agent.cli run-worker `
  --worker deepseek `
  --context-packet outputs\context_packet.json `
  --worktree . `
  --output-dir outputs\worker_deepseek `
  --task-id demo_worker `
  --experiment-id proposal_001
```

By default, a coding worker only creates proposal artifacts such as
`proposal.json` and `proposal.md`.  Passing `--apply` lets accepted
`create_or_replace` edits write into `--worktree`, but only after the path
allowlist and forbidden-path checks pass.  Even after `--apply`, the worker
result is not a success verdict; the harness must still run quick tests and the
fixed evaluator.
For DeepSeek proposals, the normalized `proposal.json` also contains
`proposal_audit`: a deterministic check of whether the proposal used
`project_intake`, which intake files it referenced, whether accepted edits touch
core solver files, and whether they touch validator or benchmark candidates.
This audit is diagnostic evidence only; it never replaces evaluator promotion.

For a full single-iteration loop, use `run-worker-cycle`. It creates an
isolated candidate worktree, runs the worker against the context packet, then
runs the fixed harness evaluator inside that candidate tree:

```powershell
python -m harness_agent.cli run-worker-cycle `
  --worker deepseek `
  --contract outputs\confirmed_contract.json `
  --context-packet outputs\context_packet.json `
  --output-dir outputs\cycle_001 `
  --project-root . `
  --experiment-id cycle_001 `
  --apply-worker
```

The command writes `cycle_result.json` and `cycle_report.md`. If the worker is
`null`, the same command validates that the isolated worktree and evaluator path
are healthy before live code generation is enabled.  Each cycle also writes
`worker_worktree_delta.json` and `worker_changes.patch`; these are generated by
the harness from candidate worktree snapshots, not from the worker's own
description of its edits.

For repeated self-evolution, use `run-worker-loop`. It runs a baseline
evaluation first, then executes multiple candidate cycles. A candidate is
promoted only when its Core evaluator-backed objective key is strictly better
than the incumbent; otherwise the round is rolled back automatically.

```powershell
python -m harness_agent.cli run-worker-loop `
  --worker deepseek `
  --contract outputs\confirmed_contract.json `
  --context-packet outputs\context_packet.json `
  --output-dir outputs\loop_001 `
  --project-root . `
  --iterations 5 `
  --apply-worker
```

The loop writes `loop_result.json` and `loop_report.md`, including baseline
metrics, each round's worker status, promotion/rollback decision, and final
incumbent worktree.  Each round also receives a refreshed
`round_xxx/context_packet.json` that contains the baseline result, current
incumbent key, previous evaluator-backed promotion/rollback decisions, and
compact instructions for avoiding repeated failed edits.  The report also
records a proposal fingerprint and duplicate-proposal flag for each round so
homogeneous worker outputs are visible without replacing the evaluator-based
promotion rule.  If the worker writes a structured proposal artifact, the loop
also carries compact proposal diagnostics into the next round: project-intake
usage, referenced files, core-solver touch points, validator/benchmark touch
points, quick-test references, and warnings.  These fields are reflection input
only; promotion remains evaluator-only.  The loop report links to each round's
worktree delta artifact and text patch for audit and later reflection.

## Project Boundary

This repository owns:

- task contract loading and validation;
- benchmark / evaluator execution;
- artifact directory layout;
- experiment ledger;
- runner status and failure classification;
- final report generation.

This repository does not own:

- FJSP domain solver rules;
- fixed evaluator correctness;
- industrial instance data;
- private API keys;
- previous manually tuned solutions.

## Intended Architecture

```text
documents + instances + evaluator
        |
        v
Task Contract
        |
        v
LangGraph Harness Core ----> CodingWorker / Solver command
        |                    |
        |                    v
        |              solution artifact
        v
Evaluator / Validator command
        |
        v
Experiment Ledger + Report
```

## Next Milestones

1. Add stronger document-to-contract extraction.
2. Add FJSP evaluator adapters for industrial variants.
3. Add hypothesis graph pruning and mutation operators.
4. Add optional Git branch/worktree archival for promoted candidates.
5. Add larger benchmark regression batches.

## Standard FJSP Smoke

The repository now includes a minimal standard-FJSP parser, baseline solver, and
evaluator.  This is the first real benchmark path beyond the dummy example.

```powershell
python -m harness_agent.cli validate-contract --contract configs\standard_fjsp_tiny.example.json
python -m harness_agent.cli run --contract configs\standard_fjsp_tiny.example.json --output-dir outputs\standard_fjsp_tiny
```

To test external public instances, create a task contract whose instance paths
point to local FJSPLib/qimingme instance files and keep using
`examples/standard_fjsp_solver.py` plus `examples/standard_fjsp_evaluator.py`.

If a best-known CSV is available, generate a contract with gap reporting:

```powershell
python -m harness_agent.cli build-standard-contract `
  --instance-dir C:\path\FJSP-Instance-main\instance `
  --pattern "fjsp.barnes*.txt" `
  --best-known-csv C:\path\Best.csv `
  --output outputs\local_barnes_contract.json `
  --seeds 0,1,2 `
  --rounds 1

python -m harness_agent.cli run `
  --contract outputs\local_barnes_contract.json `
  --output-dir outputs\standard_fjsp_barnes
```

The generated report includes `best_known_makespan` and `gap_pct` when the CSV
contains the evaluated instance name.

Harness reports also include a validation summary and a Pareto frontier over
complete candidate aggregates.  Objective keys are normalized so larger is
better for every objective, including minimized metrics after sign conversion.

## Document-Driven Standard Agent

`run-standard-agent` is the current end-to-end LangGraph loop for standard FJSP:

1. read requirement / IO / prompt Markdown documents;
2. create or request a strategy profile, including dispatch rules and optional local-search operator/budget profiles;
3. split the profile into one or more strategy candidates;
4. generate candidate task contracts;
5. run the selected solver under the fixed evaluator;
6. compare candidates by evaluator metrics;
7. write round reflections and an agent report with best-known gaps;
8. append a structured hypothesis record for the next evolution round.

DeepSeek is enabled by local secret configuration.  Do not commit API keys.
The runner checks `DEEPSEEK_API_KEY`, `DEEPSEEK_API_KEY_FILE`, and ignored
local files such as `.env` / `.env.local`:

```powershell
$env:DEEPSEEK_API_KEY="<your key>"
# or copy .env.example to .env and fill DEEPSEEK_API_KEY_FILE / DEEPSEEK_API_KEY
python -m harness_agent.cli run-standard-agent `
  --profile-mode deepseek `
  --deepseek-model deepseek-v4-pro `
  --solver local-search `
  --doc F:\path\problem.md `
  --doc F:\path\io.md `
  --instance-dir C:\path\FJSP-Instance-main\instance `
  --pattern "fjsp.barnes*.txt" `
  --best-known-csv C:\path\Best.csv `
  --output-dir outputs\standard_agent_barnes_deepseek `
  --max-rounds 3 `
  --portfolio-size 256 `
  --strategy-candidates 4 `
  --max-workers 4 `
  --local-search-restarts 2 `
  --local-search-initial-pool-size 1 `
  --local-search-iterations 100 `
  --local-search-neighbor-limit 220 `
  --local-search-time-limit-sec 4
```

For offline smoke tests, use `--profile-mode template`.  This keeps the same
agent workflow but uses a local strategy profile instead of calling DeepSeek.
Template mode is useful for validating the harness, but it is not evidence that
the LLM agent generated or reflected on rules.  Use `--profile-mode deepseek`
for agent-driven experiments; if no DeepSeek key is available, that mode fails
instead of silently falling back to the template profile.

The default standard-FJSP solver is now `local-search`: it first builds a
diverse dispatch-rule portfolio, then improves the chosen schedule with a
critical-path local search.  `--solver portfolio` keeps the constructive
portfolio-only mode for faster ablation tests.  When `--best-known-csv` is
provided, every evaluated instance reports `best_known_makespan` and `gap_pct`
if the instance file name is present in the CSV.

`--local-search-neighborhood-profile` exposes one local-search operator family
to the agent layer.  `--local-search-neighborhood-profiles` accepts a
comma-separated list and cross-evaluates those operator families against every
strategy candidate in the round.  `random` preserves the broad legacy
critical-operation neighborhood, `critical-block` evaluates critical-path
machine-block moves, and `combined` uses the legacy sampler with a bounded
critical-block supplement.  This makes neighborhood selection an evolvable rule
choice instead of a hidden implementation constant.

DeepSeek/template profiles may now include `local_search_profiles`.  When
`--local-search-run-profiles` is not provided, each strategy candidate uses those
generated local-search profiles, so the agent can evolve dispatch weights,
neighborhood family, restart count, elite constructive initial count, iteration
budget, neighbor limit, and time limit together.  `initial_pool_size` is not a
warm start: it selects multiple fresh high-quality constructive schedules from
the current portfolio before local search.  Passing `--local-search-run-profiles` intentionally overrides
model-generated settings for controlled ablations.  Built-in presets such as
`balanced-random`, `balanced-combined`, `balanced-hgtsa`, `deep-combined`, and
`deep-hgtsa` remain available as fixed evaluator-visible candidates.

In `--profile-mode deepseek`, the model is also called after evaluation to write
an evaluator-grounded round reflection.  That reflection is appended to the next
round's context together with the structured hypothesis record, so subsequent
profiles are conditioned on measured candidate wins/losses rather than on a
hand-written rule summary.  In `template` mode this reflection remains local and
is intended only for harness smoke tests.  The final `agent_report.md` records
both the last profile source and the last reflection source; use those fields to
separate true model-driven runs from local harness validation runs.

`--max-workers` controls how many independent instance/seed experiments the
harness evaluates concurrently.  It is intentionally separate from solver
parameters: higher values accelerate candidate comparison but do not change the
solution logic of any single run.

The DeepSeek client also accepts the user-facing alias `deepseek-4-pro` and
maps it to the API-supported `deepseek-v4-pro` model name.

Each run also writes `hypotheses.jsonl` in the output directory.  Every record
contains the strategy source, solver, parent hypothesis, comparable score,
delta from the previous hypothesis, summary metrics, and artifact paths.  This
is the first persistent memory layer for self-evolution.  The standard agent
also writes `hypothesis_graph.json` and `hypothesis_graph.md`, which classify
historical hypotheses as `promote`, `prune`, or `mutate` using evaluator-backed
scores.  The next round receives this graph guidance in its context, so profile
generation can preserve elite ideas, avoid failed branches, and perturb useful
parents instead of restarting from a blank prompt.

`--strategy-candidates` controls how many profile variants are evaluated inside
each round.  Candidate 0 keeps the full profile; later candidates perform
single-strategy ablations or deterministic profile mutations.  The evaluator
chooses the selected candidate, not the LLM.

## Coding Workers

`harness_agent.worker.CodingWorker` is the interface for code-generation
backends.  The repository currently ships:

- `NullWorker`: a no-op backend for contract and harness tests.
- `OpenCodeWorker`: a non-interactive `opencode run` adapter.  It becomes
  active once the `opencode` executable is available on PATH, or when a concrete
  executable path is supplied by code.  It writes prompt, command, stdout, and
  stderr artifacts; the harness still decides acceptance through worktree diff
  capture and evaluator results.

OpenCode can be selected from the same guarded worker-cycle and worker-loop
commands:

```powershell
python -m harness_agent.cli run-worker-cycle `
  --worker opencode `
  --opencode-model "provider/model" `
  --contract outputs\confirmed_contract.json `
  --context-packet outputs\context_packet.json `
  --output-dir outputs\opencode_cycle_001 `
  --project-root . `
  --experiment-id opencode_cycle_001
```

The trusted LangGraph harness owns evaluation and reporting; no coding worker is
allowed to mark its own candidate as successful.

Check local backend availability with:

```powershell
python -m harness_agent.cli worker-status
```

## Knowledge Base

The `knowledge/` directory stores paper cards, benchmark notes, and imported
local notes from the previous Huawei FJSP project.  Raw local PDFs live under
`knowledge/local_papers/raw/` and are intentionally not committed to Git.
The local paper index includes Xie Jin's HGTSA dissertation, and
`knowledge/imported_huawei_fjsp_knowledge/operators/xiejin_hgtsa_n8_k_insertion_tabu_spec.md`
summarizes the N7/N8/k-insertion, tabu-key, and approximate-evaluation details
that should guide the next local-search operator upgrade.  The corresponding
`hgtsa-lite` and `hybrid` solver profiles are experimental and evaluator-gated:
they expose the paper-inspired moves for comparison, but the `combined` profile
remains the stronger default until cross-instance evidence says otherwise.
