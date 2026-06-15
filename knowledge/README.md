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

## Imported Local Knowledge

`imported_huawei_fjsp_knowledge/` contains the Markdown cards previously built
in the Huawei FJSP project.  These imported cards include operator notes,
standard-FJSP smoke lessons, codebase notes, and paper summaries.  They are kept
separate from the new cards so the provenance remains clear.

`local_papers/` indexes raw PDFs available on this machine.  The raw PDF folder
is ignored by Git, while the index and derived notes are committed.
