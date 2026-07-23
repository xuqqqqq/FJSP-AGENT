# HeurAgenix

## Source

- Code: [microsoft/HeurAgenix](https://github.com/microsoft/HeurAgenix)
- Paper summary: [HeurAgenix on Hugging Face Papers](https://huggingface.co/papers/2506.15196)
- Paper page: [HeurAgenix OpenReview](https://openreview.net/forum?id=xxSK3ZNAhh)

## Relevant Idea

HeurAgenix is described as an LLM-driven hyper-heuristic framework that evolves
a pool of heuristics and then selects among them for problem states.  This is
closer to our target than one-shot solver generation because it separates:

- heuristic generation;
- heuristic evaluation;
- heuristic selection;
- state-aware use of a heuristic pool.

## Impact on FJSP Harness Agent

The harness should avoid treating each generated solver as an isolated artifact.
It should learn a reusable pool of FJSP strategy fragments:

- dispatching rules;
- machine selection rules;
- route selection rules;
- setup-reduction rules;
- batch construction rules;
- local search operators.

The future selector can be a simple bandit, a learned policy, or an LLM-based
selector.  The first implementation should keep the selector outside the trusted
validator path.

## Module Mapping

- `Strategy Library`: stores reusable rule fragments.
- `Hypothesis Graph`: tracks evolution and compatibility of fragments.
- `Policy Recommender`: selects top-k strategy configurations for a new case.
- `Benchmark Runner`: compares selected strategies under the same evaluator.

