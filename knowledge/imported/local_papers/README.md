# Local Paper Index

This folder indexes local papers available on this machine.  Raw PDF files are
stored under `knowledge/imported/local_papers/raw/` for local reading and retrieval, but
that directory is intentionally ignored by Git because many papers may not be
redistributable in a public repository.

## Imported Local PDFs

| Paper file | Why it matters for FJSP Harness Agent |
| --- | --- |
| `FJSP场景调研报告10-17.pdf` | Local scene survey covering standard FJSP, aluminum rolling, semiconductor manufacturing, constraint variants, solver families, and AI/LLM heuristic evolution; indexed by `knowledge/references/general_fjsp/fjsp_scene_survey_2025_10_17.md`. |
| `基于混合遗传禁忌搜索算法的作业车间调度方法_谢晋 (1).pdf` | Hybrid genetic/tabu-search workflow; useful for neighborhood and tabu-loop strategy cards. |
| `2025_RCIM_A_disjunctive_graph_based_metaheuristic_for_flexible_job_shop_scheduling_problems_considering_fixture_shortages.pdf` | Disjunctive-graph metaheuristic for FJSP variants with resource shortages; useful for industrial extensions. |
| `智能制造系统中柔性作业车间调度的有效局部搜索算法.pdf` | Local-search design for FJSP; useful for operator library. |
| `大规模柔性作业车间调度问题分解建模和求解方法_刘海涛.pdf` | Large-scale decomposition; useful for industrial-size scheduling. |
| `基于End-to-end分层强化学习的大规模动态柔性作业车间调度问题研究_雷坤.pdf` | Hierarchical RL for large-scale dynamic FJSP; useful for future learning-worker design. |
| `基于双层注意力网络的强化学习方法求解柔性作业车间调度问题_王皓焱.pdf` | Attention-based RL for FJSP; useful for state/action representation ideas. |
| `基于改进DQN算法的柔性作业车间调度问题_王强.pdf` | DQN-style dispatching reference; useful for lightweight policy learning. |
| `基于深度强化学习的大规模柔性作业车间调度问题研究_郑婷娟.pdf` | DRL for large-scale FJSP; useful for generalization discussion. |
| `柔性作业车间中图嵌入的深度强化调度策略研究_陈明童.pdf` | Graph embedding for FJSP dispatching; useful for graph-state context packets. |
| `融合深度强化学习和流体模型的柔性车间动态调度方法研究_丁林山.pdf` | Hybrid DRL/fluid model scheduling; useful for dynamic scheduling variants. |

## Next Knowledge Tasks

1. Read each PDF and create a short knowledge card under `knowledge/references/` or
   `knowledge/operators/`.
2. Link every operator card to a harness module: parser, evaluator, strategy
   library, hypothesis graph, or worker prompt.
3. Keep raw PDF filenames stable so local retrieval scripts can locate them.
