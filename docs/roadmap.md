# Roadmap Toward the AlgoForge-Style FJSP Agent

## Current Status

The repository has reached the first harness milestone:

- independent Git repository;
- task-contract driven CLI;
- solver/evaluator command orchestration;
- SQLite experiment ledger;
- report generation;
- standard FJSP parser, baseline solver, and evaluator;
- LangGraph `run-standard-agent` loop for document-driven strategy-profile
  generation, contract creation, evaluation, and reflection;
- portfolio dispatch solver with optional DeepSeek/template strategy profiles;
- critical-path local-search solver on top of the dispatch portfolio;
- best-known CSV gap reporting for standard FJSP benchmarks;
- local knowledge base with paper cards and imported Huawei FJSP notes.

This is not yet the full MD requirement.  It is the engineering base that makes
the later self-evolution loop measurable and auditable.

## Verified Smoke Tests

### Dummy Harness Flow

```powershell
python -m harness_agent.cli run --contract configs\task_contract.example.json --output-dir outputs\demo_verify
```

Result:

- total experiments: 4
- valid experiments: 4
- failed experiments: 0

### Included Standard FJSP Tiny Instance

```powershell
python -m harness_agent.cli run --contract configs\standard_fjsp_tiny.example.json --output-dir outputs\standard_fjsp_tiny_v3
```

Result:

- total experiments: 3
- valid experiments: 3
- failed experiments: 0
- best makespan: 7

### Local qimingme Barnes Smoke

Local-only contract:

```powershell
python -m harness_agent.cli run --contract outputs\local_barnes_contract.json --output-dir outputs\standard_fjsp_barnes_local_smoke_v2
```

Result:

- instances: 3 Barnes instances from local qimingme/FJSP-Instance files;
- seeds: 2;
- total experiments: 6;
- valid experiments: 6;
- failed experiments: 0;
- candidate aggregate average makespan: 1258.33.

The local contract is not committed because it contains machine-specific
absolute paths.

### Document-Driven Standard Agent Smoke

Template profile command:

```powershell
python -m harness_agent.cli run-standard-agent `
  --profile-mode template `
  --pattern "fjsp.barnes.mt10*.txt" `
  --max-instances 3 `
  --seeds 0,1 `
  --portfolio-size 256
```

Result from the local Barnes smoke set:

- total experiments: 6;
- valid experiments: 6;
- failed experiments: 0;
- average best-known gap: 15.115%;
- best single-instance gap: 11.319%.

This improves the earlier single-rule ECT smoke average gap of 36.983% on the
same 3-instance slice.  It is still not a near-best solver; the next quality
step is to add code-level operator evolution and DeepSeek-driven
strategy-profile mutation across rounds.

### Local-Search Standard FJSP Smoke

The local-search solver starts from the portfolio result and then applies
critical-path neighborhood moves under the same fixed evaluator.

Barnes full-family command shape:

```powershell
python -m harness_agent.cli run-standard-agent `
  --profile-mode template `
  --solver local-search `
  --pattern "fjsp.barnes*.txt" `
  --seeds 0 `
  --portfolio-size 192 `
  --local-search-restarts 2 `
  --local-search-iterations 100 `
  --local-search-neighbor-limit 220 `
  --local-search-time-limit-sec 4
```

Result from the local Barnes set:

- instances: 21;
- valid experiments: 21;
- failed experiments: 0;
- average best-known gap: 9.257%;
- best single-instance gap: 7.438%.

Dauzere/DP-family smoke:

- instances: 18;
- valid experiments: 18;
- failed experiments: 0;
- average best-known gap: 11.289%;
- best single-instance gap: 5.558%.

These are still baseline-quality results rather than competitive final results,
but the harness now exposes a measurable gap against the provided best-known
table for any standard instance whose file name appears in the CSV.

## Gap to the Target MD

| Requirement | Current status | Next action |
| --- | --- | --- |
| Read requirement and IO documents | Not implemented | Add document ingestion and extraction. |
| Derive Task Contract from documents | Manual JSON only | Build `contract_builder` with source references. |
| Support multiple metrics from documents | Contract model supports it | Add evaluator schema checks and Pareto reporting. |
| Generate solver code with an LLM worker | Strategy profile generation implemented; code edits not enabled | Add guarded DeepSeek/OpenCode code-edit loop. |
| Strategy-first evolution | Implemented for standard FJSP profiles | Extend from scoring profiles to code-level operator evolution. |
| Self-reflection and hypothesis graph | Not implemented | Add `hypotheses` table and reflection node. |
| Rule/operator evolution | Local-search operator exists; LLM operator edits not enabled | Add strategy library and mutation operators. |
| Standard FJSP benchmark testing | Smoke path and best-known gap reporting implemented | Add larger benchmark batches and regression baselines. |
| Industrial FJSP variant testing | Not implemented here | Add adapter to external industrial evaluator. |
| Full auditability | Ledger exists | Add Git worktree, diff capture, context packet hash. |

## Next Build Slice

The next concrete slice should be:

1. `contract_builder.py`
   - inputs: requirement docs, IO docs, metric docs;
   - output: draft `task_contract.json`;
   - records source passages and uncertain fields.

2. `context_packet.py`
   - packages task contract, evaluator protocol, knowledge cards, and current hypothesis;
   - keeps prompts small to avoid token truncation.

3. `workers/deepseek_worker.py`
   - current: generates `strategy.md` and `strategy_profile.json`;
   - next: generate candidate solver patches behind static guards;
   - returns structured result only;
   - never marks itself successful.

4. `hypothesis.py`
   - records strategy family, parent hypothesis, mutation type, and status;
   - supports prune/promote/mutate.

5. `standard_fjsp_batch.py`
   - builds task contracts from instance directory and filename patterns;
   - optionally attaches best-known-solution CSV;
   - produces makespan and gap reports.

## Design Rule

The harness must remain the trusted layer.  LLM workers may propose and modify
algorithms, but evaluator execution, validity status, metric comparison, ledger
updates, and final reporting must stay in deterministic harness code.
