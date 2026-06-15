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
2. create or request a strategy profile;
3. split the profile into one or more strategy candidates;
4. generate candidate task contracts;
5. run the selected solver under the fixed evaluator;
6. compare candidates by evaluator metrics;
7. write round reflections and an agent report with best-known gaps;
8. append a structured hypothesis record for the next evolution round.

DeepSeek is enabled by environment variable only.  Do not commit API keys:

```powershell
$env:DEEPSEEK_API_KEY="<your key>"
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
  --local-search-iterations 100 `
  --local-search-neighbor-limit 220 `
  --local-search-time-limit-sec 4 `
  --local-search-run-profiles balanced-random,balanced-combined
```

For offline smoke tests, use `--profile-mode template`.  This keeps the same
agent workflow but uses a local strategy profile instead of calling DeepSeek.

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

`--local-search-run-profiles` extends that idea to full solver settings.  Built
in presets such as `balanced-random`, `balanced-combined`, and `deep-combined`
bundle portfolio size, restart count, iteration budget, neighbor limit, time
limit, and neighborhood profile into evaluator-visible candidates.  This is the
current lightweight path toward instance-adaptive parameter and rule selection.

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
that should guide the next local-search operator upgrade.
