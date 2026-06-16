# Roadmap Toward the AlgoForge-Style FJSP Agent

## Current Status

The repository has reached the first harness milestone:

- independent Git repository;
- task-contract driven CLI;
- solver/evaluator command orchestration;
- SQLite experiment ledger;
- report generation;
- evaluator metric schema checks, validation summaries, and Pareto frontier
  reporting for multi-objective contracts;
- standard FJSP parser, baseline solver, and evaluator;
- LangGraph `run-standard-agent` loop for document-driven strategy-profile
  generation, contract creation, evaluation, and reflection;
- portfolio dispatch solver with optional DeepSeek/template strategy profiles;
- critical-path local-search solver on top of the dispatch portfolio;
- best-known CSV gap reporting for standard FJSP benchmarks;
- JSONL hypothesis ledger for round-to-round strategy memory;
- hypothesis graph summaries with evaluator-backed promote/prune/mutate
  guidance for the next round;
- per-round strategy-candidate evaluation with ablation/mutation variants;
- local knowledge base with paper cards and imported Huawei FJSP notes.
- document-to-draft-contract CLI that records source references, uncertain
  fields, and required human confirmation before a generated evaluator or
  validator can become formal.
- source-grounded draft-contract extraction of problem features, metric hints,
  command-template checks, document statistics, and confirmation checklist.
- formal run gate that refuses unconfirmed generated contracts unless the user
  explicitly requests exploratory `--allow-draft` execution.
- contract health-check CLI that validates referenced inputs, runs the quick
  test, and repeats a small benchmark probe to detect unstable evaluator
  behavior before optimization.
- intent-alignment CLI that turns a contract plus health-check evidence into a
  reviewable optimization intent card with objectives, constraints, risks,
  blockers, warnings, and readiness status.
- context-packet CLI that packages confirmed evaluator semantics, bounded docs,
  knowledge cards, previous reports, and hypotheses for coding workers.
- `run-worker` CLI that executes a coding backend against a context packet and
  stores proposal artifacts, with optional guarded apply inside a specified
  worktree.
- `run-worker-cycle` CLI that copies an isolated candidate worktree, runs a
  coding worker, optionally applies accepted edits, and reruns the Core harness
  evaluator in that candidate tree.
- `run-worker-loop` CLI that repeats candidate cycles, compares each candidate
  against the incumbent objective key, and records promotion/rollback decisions.
- per-round context refresh in `run-worker-loop`, so each coding-worker proposal
  can see baseline metrics, current incumbent status, and previous
  evaluator-backed wins/losses.
- `run-standard-pipeline` CLI that optionally runs health-check and
  intent-alignment first, blocks optimization stages when admission fails, then
  runs the standard benchmark suite, evaluator-backed coding-worker loop, and
  evidence index as one reproducible standard-FJSP smoke workflow.
- harness-generated worktree delta and unified patch artifacts for each
  worker-cycle candidate.
- non-interactive OpenCodeWorker adapter that can execute `opencode run` inside
  guarded candidate worktrees when OpenCode is installed.
- evidence-index CLI that scans generated health-check, intent-alignment, demo,
  suite, and worker-loop manifests and writes one JSON/Markdown audit index.
- project-intake CLI that scans the repository before optimization and records
  language mix, Git state, entry/core/dependency/benchmark/validator files,
  inferred test commands, data directories, edit policy, context snippets, and
  risk flags.
- `run-standard-pipeline` now starts with project intake and includes that
  manifest in the final evidence index.

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
  --local-search-time-limit-sec 4 `
  --local-search-neighborhood-profile combined
```

Result from the local Barnes set with the legacy random neighborhood:

- instances: 21;
- valid experiments: 21;
- failed experiments: 0;
- average best-known gap: 9.257%;
- best single-instance gap: 7.438%.

After exposing the neighborhood profile and using `combined`, which keeps the
legacy broad critical-operation sampler while adding bounded critical-block
moves, the same Barnes family smoke produced:

- instances: 21;
- valid experiments: 21;
- failed experiments: 0;
- average best-known gap: 8.754%;
- best single-instance gap: 5.313%.

Dauzere/DP-family smoke:

- instances: 18;
- valid experiments: 18;
- failed experiments: 0;
- average best-known gap: 11.289%;
- best single-instance gap: 5.558%.

These are still baseline-quality results rather than competitive final results,
but the harness now exposes a measurable gap against the provided best-known
table for any standard instance whose file name appears in the CSV.

### DeepSeek Strategy-Candidate Smoke

DeepSeek live API was verified through the standard-agent path using the
environment variable `DEEPSEEK_API_KEY`.  The public-facing model alias
`deepseek-4-pro` is normalized to the API-supported `deepseek-v4-pro`.

Small single-instance smoke:

- profile source: DeepSeek;
- strategy candidates per round: 2;
- valid experiments: 1/1;
- selected candidate: SPT-style single-strategy ablation;
- output included `hypotheses.jsonl` with candidate comparison.

Two-round smoke:

- profile source: DeepSeek;
- strategy candidates per round: 2;
- second-round rationale consumed the previous `avg_gap_pct` feedback;
- valid experiments: 2/2 across the two rounds;
- no quality improvement on the tiny smoke, so the next work should improve
  mutation quality rather than claiming benchmark convergence.

## Gap to the Target MD

| Requirement | Current status | Next action |
| --- | --- | --- |
| Read requirement and IO documents | Draft-contract ingestion with source-grounded feature/metric hints, uncertainty reporting, project intake, and intent-alignment cards implemented | Add richer section-level parsing and optional LLM-assisted extraction. |
| Derive Task Contract from documents | Draft JSON, evidence fields, command checks, and confirmation gate implemented | Add stronger document schema extraction and web review workflow. |
| Support multiple metrics from documents | Contract model, evaluator schema checks, validation summary, and Pareto frontier reporting implemented | Add richer evaluator schema declarations and visual Pareto exports. |
| Generate solver code with an LLM worker | DeepSeek proposal-first worker, non-interactive OpenCodeWorker adapter, isolated worker-cycle evaluator rerun, multi-cycle promotion/rollback, and per-round context refresh implemented | Add stronger rule/operator mutation prompts. |
| Admission and intent gates | Project-intake, health-check, and intent-alignment readiness implemented; standard pipeline skips optimization stages when admission fails | Add UI confirmation flow around the generated readiness card. |
| Strategy-first evolution | DeepSeek/template profiles plus candidate ablation implemented | Extend from scoring profiles to code-level operator evolution. |
| Self-reflection and hypothesis graph | JSONL records plus promote/prune/mutate graph summaries implemented | Add richer operator-level lineage and graph-aware code mutation. |
| Rule/operator evolution | Local-search operator and profile-level mutation exist; LLM operator edits not enabled | Add guarded code-level mutation operators. |
| Standard FJSP benchmark testing | Smoke path and best-known gap reporting implemented | Add larger benchmark batches and regression baselines. |
| Industrial FJSP variant testing | Not implemented here | Add adapter to external industrial evaluator. |
| Full auditability | Ledger, context packet hash, candidate worktree copy, worktree delta JSON, text patch artifacts, and evidence index implemented | Add optional Git worktree/branch archival for long-running external experiments. |

## Next Build Slice

The next concrete slice should be:

1. `contract_builder.py`
   - current: reads requirement docs, IO docs, metric docs, instances, and CLI
     hints;
   - current: outputs review-required draft `task_contract.json`;
   - current: records source references, uncertain fields, problem-feature
     hints, metric hints, command-template checks, and confirmation checklist;
   - next: add section-level document schema extraction and metric-specific
     validator prompts.

2. `context_packet.py`
   - current: packages task contract hash, review status, evaluator protocol,
     knowledge cards, previous report, and current hypothesis;
   - current: keeps prompts bounded to avoid token truncation;
   - current: refreshes round context with evaluator-backed loop feedback before
     each worker-loop candidate cycle;
   - next: make refreshed packets more compact for long industrial documents.

3. `workers/deepseek_worker.py`
   - current: generates `strategy.md` and `strategy_profile.json`, with JSON
     repair and model-name aliasing;
   - current: consumes context packets and writes guarded code-edit proposals;
   - current: optional apply is limited by allowed/forbidden path checks;
   - current: proposals can be evaluated through `run-worker-cycle` in an
     isolated candidate tree;
   - next: add stronger proposal diversity checks;
   - returns structured result only;
   - never marks itself successful.

4. `hypothesis.py`
   - current: records strategy source, parent hypothesis, score, delta, summary,
     and artifact paths;
   - current: summarizes records into promote/prune/mutate guidance for the next
     round;
   - next: track operator-level lineage and attach graph decisions to code
     mutation proposals.

5. `strategy_variants.py`
   - current: creates full-profile, single-strategy, and deterministic mutated
     profile candidates;
   - next: use hypothesis history to generate targeted profile mutations.

5. `standard_fjsp_batch.py`
   - builds task contracts from instance directory and filename patterns;
   - optionally attaches best-known-solution CSV;
   - produces makespan and gap reports.

## Design Rule

The harness must remain the trusted layer.  LLM workers may propose and modify
algorithms, but evaluator execution, validity status, metric comparison, ledger
updates, and final reporting must stay in deterministic harness code.
