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
stores source references plus uncertain fields in the contract JSON.  After
review, create a confirmed copy before treating the evaluator as formal:

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
  --previous-report outputs\demo\report.md `
  --hypothesis "Try a conservative solver improvement under confirmed evaluator semantics." `
  --output outputs\context_packet.json
```

The packet records the contract hash, review status, evaluator protocol, edit
policy, bounded document snippets, knowledge cards, previous report, and worker
instructions.  A worker may self-test against it, but AlgoForge Core still owns
the final evaluator run and success verdict.

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
are healthy before live code generation is enabled.

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
incumbent worktree.

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

1. Wire the `OpenCodeWorker` adapter into candidate code-generation nodes.
2. Add document-to-contract extraction.
3. Add FJSP evaluator adapters for industrial variants.
4. Add hypothesis graph and reflection summaries.
5. Add Git worktree isolation for each candidate.

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
is the first persistent memory layer for self-evolution; later code-generation
workers can use the same ledger to decide what to mutate or reject.

`--strategy-candidates` controls how many profile variants are evaluated inside
each round.  Candidate 0 keeps the full profile; later candidates perform
single-strategy ablations or deterministic profile mutations.  The evaluator
chooses the selected candidate, not the LLM.

## Coding Workers

`harness_agent.worker.CodingWorker` is the interface for code-generation
backends.  The repository currently ships:

- `NullWorker`: a no-op backend for contract and harness tests.
- `OpenCodeWorker`: a detection/adapter boundary for introducing OpenCode as a
  coding agent.  It becomes active once the `opencode` executable is available
  on PATH.

At this stage, OpenCode is intentionally only an adapter boundary.  The trusted
LangGraph harness owns evaluation and reporting; a coding worker may later be
allowed to propose solver edits, but it will not be allowed to mark its own
candidate as successful.

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
