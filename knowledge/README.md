# FJSP Harness Agent Knowledge Base

This folder stores concise, source-linked knowledge cards used by the harness
agent roadmap.  It is not a dump of full papers.  Each card should answer:

1. What is the source?
2. What is the relevant idea?
3. How does it affect FJSP Harness Agent design?
4. Which module should use it?

The knowledge base should stay auditable.  Every factual claim that affects the
design should point to a paper, benchmark page, repository, or local experiment.

## Initial Cards

| Card | Role |
| --- | --- |
| `papers/eoh.md` | LLM + evolutionary heuristic design pattern. |
| `papers/heuragenix.md` | Multi-agent heuristic evolution and selector pattern. |
| `papers/doagnn_fjsp_rl.md` | FJSP-specific reinforcement learning reference. |
| `benchmarks/fjsplib.md` | Standard FJSP benchmark families and best-known-solution source. |
| `benchmarks/standard_fjsp_format.md` | Public text-format contract for standard FJSP instances. |
| `principles/harness_agent_design.md` | Core design principles derived from the above sources. |

## Retrieval Order

For standard FJSP benchmark work, retrieve:

1. `benchmarks/fjsp_benchmark_scope.md`
2. `papers/fjsp_agent_current_capability_20260704.md`
3. family/operator cards selected by tags such as `critical_block`,
   `machine_reassignment`, `tabu_search`, or `portfolio`

For FJSP-SDST / HUdata work, retrieve:

1. `papers/fjsp_agent_current_capability_20260704.md`
2. `papers/fjsp_sdst_agent_generated_search_memory_20260707.md` when the worker
   is creating or improving an agent-generated solver rather than adapting AWLS
   slots
3. `papers/awls_sdst_hudata20_baseline_notes.md`
4. only the selected slot's notes, such as
   `awls_sdst_initialization_notes.md` or `awls_sdst_move_evaluation_notes.md`

LB/UB cards are comparison references.  They should guide benchmark selection
and reporting, not solver scoring.

For new FJSP variants or industrial constraints, retrieve:

1. `principles/fjsp_variant_domain_pack_rag.md`
2. `papers/fjsp_scene_survey_2025_10_17.md`
3. the active IO/evaluator contract and only the selected slot notes

Do not put variant algorithms in backend orchestration code.  Put them in
domain packs, knowledge cards, skills, slot manifests, or worker context.

## Imported Local Knowledge

`imported_huawei_fjsp_knowledge/` contains the Markdown cards previously built
in the Huawei FJSP project.  These imported cards include operator notes,
standard-FJSP smoke lessons, codebase notes, and paper summaries.  They are kept
separate from the new cards so the provenance remains clear.

`local_papers/` indexes raw PDFs available on this machine.  The raw PDF folder
is ignored by Git, while the index and derived notes are committed.
