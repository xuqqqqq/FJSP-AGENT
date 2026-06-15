# DOAGNN for FJSP Reinforcement Learning

## Source

- ACM page: [Dual Operation Aggregation Graph Neural Networks for Solving Flexible Job-Shop Scheduling Problem with Reinforcement Learning](https://dl.acm.org/doi/10.1145/3696410.3714616)
- OpenReview page: [DOAGNN WWW 2025](https://openreview.net/forum?id=AWu0bCMVgR)
- Code: [thxiwilldoit/DOAGNN](https://github.com/thxiwilldoit/DOAGNN)

## Relevant Idea

DOAGNN is an FJSP-specific reinforcement learning direction.  Its key relevance
is that FJSP decisions are naturally graph-structured:

- operations have precedence arcs;
- candidate machines create assignment alternatives;
- machine conflicts create disjunctive relations;
- dispatching actions can be learned over graph features.

## Impact on FJSP Harness Agent

The harness should not hard-code only scalar heuristic parameters.  It should
leave room for learned policy workers that use structured FJSP states.

For MVP, the practical step is modest:

- expose parsed FJSP state features in Context Packets;
- let workers propose rule changes using graph concepts such as critical path,
  machine block, operation readiness, and remaining workload;
- later allow a policy backend to output action scores.

## Module Mapping

- `FJSP Parser`: builds operation-machine candidate graph.
- `Context Builder`: exports compact graph/state summaries.
- `PolicyWorker`: future backend for PPO/GNN-style policies.
- `Evaluator`: remains the final judge regardless of policy type.

