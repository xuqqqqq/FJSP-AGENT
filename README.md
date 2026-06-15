# FJSP Harness Agent

FJSP Harness Agent is an independent engineering project for running algorithm
self-evolution experiments under fixed evaluators.

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
Harness Core ----> CodingWorker / Solver command
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

1. Add a `CodingWorker` backend that can call DeepSeek/Codex/OpenCode.
2. Add document-to-contract extraction.
3. Add FJSP evaluator adapters for standard FJSP and industrial variants.
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

## Knowledge Base

The `knowledge/` directory stores paper cards, benchmark notes, and imported
local notes from the previous Huawei FJSP project.  Raw local PDFs live under
`knowledge/local_papers/raw/` and are intentionally not committed to Git.
