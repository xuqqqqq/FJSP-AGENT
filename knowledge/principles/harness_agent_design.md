# Harness Agent Design Principles

## Principle 1: Evaluator Is the Source of Truth

LLM workers may write code and explain results, but their claims are not final.
Only the harness-run evaluator result can mark a candidate as valid or improved.

## Principle 2: Strategy Before Code

Following EoH-style automatic heuristic design, workers should first express the
heuristic idea in natural language.  This makes later reflection and crossover
possible.

## Principle 3: Evolve Rule Fragments, Not Only Parameters

The agent must be able to modify dispatching rules, local-search operators,
route-choice logic, and batching logic.  Numeric parameter tuning is only one
kind of mutation.

## Principle 4: Standard FJSP First, Industrial Variants Next

Standard FJSP makes the harness measurable with public instances and makespan.
Industrial variants then test whether the same harness can absorb new documents,
metrics, and constraints.

## Principle 5: Knowledge Cards Are Advisory

The knowledge base guides strategy generation and context construction.  It
must not override the task contract or evaluator.

## Principle 6: Instance Diagnostics Guide Strategy Only

Parsed instance profiles, setup-time ratios, and best-known/LB/UB references
help workers choose an appropriate slot strategy.  They are not objective
functions; promotion remains evaluator-backed makespan improvement.
