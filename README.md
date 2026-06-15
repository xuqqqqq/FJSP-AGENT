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

## Coding Workers

`harness_agent.worker.CodingWorker` is the interface for code-generation
backends.  The repository currently ships:

- `NullWorker`: a no-op backend for contract and harness tests.
- `OpenCodeWorker`: a detection/adapter boundary for introducing OpenCode as a
  coding agent.  It becomes active once the `opencode` executable is available
  on PATH.

Check local backend availability with:

```powershell
python -m harness_agent.cli worker-status
```

## Knowledge Base

The `knowledge/` directory stores paper cards, benchmark notes, and imported
local notes from the previous Huawei FJSP project.  Raw local PDFs live under
`knowledge/local_papers/raw/` and are intentionally not committed to Git.
