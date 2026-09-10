---
name: huawei-aluminum-fjsp
description: Industrial FJSP dispatch and finite-batch tuning guidance adapted from the Huawei aluminum workflow. Use only when an industrial_json WorkerAssignment authorizes this skill to generate or improve an independent solver under the frozen IO and Core evaluator.
metadata:
  upstream_skill: huawei-aluminum-fjsp
  adaptation: agent-generated-industrial-json
---

# Industrial Dispatch and Batch Optimization

## Scope and Source

This adapts the original Huawei aluminum Skill's dispatch, batching, parameter
search and failure-diagnosis workflow. It does not import preset solvers,
historical solutions, tuned constants or the separate complex-stepwise Skill.
The original workflow assumes existing solver/tuner commands. In this platform,
implement the equivalent bounded search inside the assigned standalone solver.

Use the method family and edit scope selected by Main. This skill supplies
industrial decisions, not an additional lane or permission to replace the
assigned method. A constructive lane can search dispatch parameters; local,
population and exact lanes can use the same industrial signals within their
assigned neighborhoods, individuals or bounded repair models.

## Frozen Contract

- Read the authorized requirement, IO and input facts. Preserve industrial JSON
  IDs and time conventions; do not substitute a standard benchmark parser.
- The current objective is strict lexicographic: maximize completed weight
  within the absolute output horizon, then minimize positive ordinary-machine
  setup transitions over the complete schedule. A scoring penalty can guide
  candidate generation, but cannot replace this final comparison.
- Complete every selected route, including tasks finishing beyond the horizon.
  Never discard late tasks to improve the reported output metric.
- Finite batches, hard Q-time bounds, maintenance, transit and directed factory
  permissions remain hard constraints. Do not use an infinite-batch or soft
  Q-time relaxation as a final candidate under this contract.
- Core alone invokes the fixed external validator and decides promotion. Do
  not locate, read, import or execute old solvers, tuners, validator source or
  saved schedules. Worker tools and smoke limits come from the assignment.

## Start From a Complete Legal Construction

Preserve the incumbent parser, decoder, export and feasibility guards unless
feedback identifies a specific defect. Keep an independent complete fallback
before evaluating new choices. Parameterize the existing construction only as
far as needed for the assigned mechanism; do not rewrite it wholesale.

When construction is needed, select a complete route and compare feasible
operation/machine placements using actual readiness, processing, setup and
transport effects. Rank feasible candidates rather than hiding hard constraints
inside a penalty. Under upper Q-time, a later maintenance or capacity conflict
can require revising the earlier part of a chain; simply delaying one operation
is not a general repair. Recheck the affected chain and machine/batch sequence.

## Dispatch Parameter Search

Start with a small, structurally diverse family of dispatch choices, then refine
around the best complete legal candidates. Useful input-derived signals are:

- lookahead: the remaining route and downstream bottleneck pressure, not just
  the next operation's processing time;
- urgency and horizon pressure: remaining feasible completion slack relative
  to the declared absolute horizon;
- output density: final task weight relative to remaining processing or scarce
  machine demand, with scales derived from the current input;
- family continuity and setup penalties: actual directed positive setup arcs
  between adjacent ordinary operations, not task adjacency or family names alone;
- critical-chain pressure and machine load: use a cheap remaining-chain or
  bottleneck proxy to avoid blocking a more valuable completion downstream;
  this does not require building a new critical-path subsystem.

Sample rule combinations or bounded parameter perturbations rather than
hardcoding one instance's weights or task bonuses. Compare complete schedules
by the frozen lexicographic objective. Retain low-setup and high-output diversity
as optional search material, but do not use Pareto dominance to override the
declared returned incumbent. Different parameter vectors that produce the same
route/assignment/order should not be counted as distinct constructions.

## Finite-Batch Search

Search grouping wait, compatible-member selection and machine choice only within
the active IO. A batch shares machine, start and finish; family equality is exact,
capacity is the minimum integer member capacity, and duration is the maximum
member duration. A singleton is legal, but does not prove a grouping mechanism.

Compare immediate singleton dispatch with bounded waiting for a compatible
member. Balance capacity use and setup/flow effects against horizon completion
and each member's Q-time slack. Apply release, transport and calendar feasibility
to every member. Adding or removing a member can change the common duration and
invalidate downstream timing, so internally check the full affected candidate
before accepting it; formal validation remains Core's responsibility.
Ordinary-machine setup arcs do not become batch setup arcs.

## Bounded Refinement

After identifying a bottleneck, vary an input-derived task priority/deferment,
route preference, machine penalty or critical-chain rule. Keep parameter families
small enough to evaluate real alternatives before the deadline. For larger
instances, shortlist operations or machines and cache immutable input facts;
avoid unlimited all-pairs or full cross-product searches.

Use the selected method family's search structure. Retain the best complete legal
incumbent across unsuccessful moves and restarts. An internal tuning loop uses
the solver's assigned deadline; it is not permission to run extra external
benchmarks, seeds or model calls.

## Diagnose Before Expanding

- Missing operations: repair route reconstruction or export completeness.
- Precedence/transfer failure: repair chain readiness or directed factory choice.
- Q-time failure: inspect both bounds, anchors and coupled chain propagation.
- Maintenance failure: check the entire processing interval and time origin.
- Ordinary overlap/setup failure: inspect actual machine predecessor/successor arcs.
- Batch failure: check exact family, member capacity and common max duration.
- Legal but weak output: revise dispatch, grouping or the assigned search operator.

Implement one bounded change, connect it to the actual CLI execution, then run
the authorized compile and smoke after the final edit. A helper definition or
parameter field without a reachable caller is not an implemented search.
Record actual candidates, accepted improvements and method counters in output
diagnostics; never invent counters or trust self-reported scores as Core evidence.
Report the changed rule, observed execution, legality feedback and remaining
failure class. Preserve successful and failed attempts for Main's reflection.
