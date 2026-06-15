# Evolution of Heuristics (EoH)

## Source

- Paper: [Evolution of Heuristics: Towards Efficient Automatic Algorithm Design Using Large Language Model](https://arxiv.org/html/2401.02051v3)
- Code: [FeiLiu36/EOH](https://github.com/FeiLiu36/EOH)

## Relevant Idea

EoH combines large language models with evolutionary computation for automatic
heuristic design.  The important pattern for this project is not a specific
heuristic, but the representation and evolution loop:

- represent a heuristic idea in natural language;
- turn the idea into executable code;
- evaluate the code with an external objective;
- select, mutate, and recombine promising heuristic ideas.

## Impact on FJSP Harness Agent

FJSP Harness Agent should preserve a strategy-first workflow:

1. Worker writes `strategy.md`.
2. Worker writes or patches solver code.
3. Harness runs fixed evaluator.
4. Experiment Ledger records whether the strategy improved or failed.
5. Hypothesis Graph evolves strategy families, not only numeric parameters.

This supports the user's requirement that the agent can change rules and
operators, not merely tune command-line parameters.

## Module Mapping

- `Context Packet`: include current hypothesis and relevant prior strategies.
- `CodingWorker`: require natural-language strategy before code.
- `Experiment Ledger`: record strategy lineage and objective metrics.
- `Hypothesis Graph`: support mutation, crossover, pruning, and promotion.

