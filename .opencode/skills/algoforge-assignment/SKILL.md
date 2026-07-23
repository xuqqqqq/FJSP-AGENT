---
name: algoforge-assignment
description: Execute AlgoForge's strict Main-Agent-to-Coding-Worker handoff using a validated WorkerAssignment without exposing the full Context Packet to the worker.
---

# AlgoForge Assignment Contract

Use this skill when the task needs a strict split between planning and execution.

## Roles

- `algoforge-main` diagnoses, selects direction, and emits the handoff.
- `requirements-method-analyst` and `evidence-analyst` are read-only support subagents. The harness exposes at most one of them per Main invocation.
- `algoforge-worker` executes only the supplied assignment.

## Required handoff

The handoff must be exactly one JSON object with these top-level keys:

```json
{
  "direction_plan": {},
  "worker_assignment": {}
}
```

`direction_plan` must contain diagnosis, alternatives, selection rationale, one
primary canonical method family plus only evidence-backed complementary families,
an optional exact method package, preservation rules, complete implementation
order, deliverables, checks, stop conditions, and a completion rule.

For an incumbent improvement round it must also contain a structured incumbent
assessment and one falsifiable next mutation. The assessment must separate
audited existing capabilities from concrete implementation limits and unknowns;
the mutation must name the existing symbols/configurations it changes and the
measurements that would disprove the bottleneck hypothesis.

Main emits Simplified Chinese commentary while it inspects evidence, compares
alternatives, and reaches a decision. These native model events are the primary
Main Agent thinking process shown in the user interface. The final JSON also
contains a bounded `reasoning_trace` for audit and fallback; it must not be used
to impersonate live commentary when native commentary exists. Neither channel
may invent tool runs or measurements absent from the PlanningPacket.

The Harness validates and compiles the handoff into `WorkerAssignment`. That
compiled object is the Worker's sole planning input and defines its exact
`target_file`, `read_set`, deliverables, implementation order, preservation and
forbidden rules, latest feedback, checks, budgets, runtime contract, lineage, and
trusted IDs of matched Worker Implementation Skills. The Worker loads those Skills
itself; Main never supplies Skill filesystem paths or copies Skill prose into the
assignment.

## Guardrails

- `algoforge-main` is read-only and may use `task` only for the single analyst subagent enabled by the harness for that invocation.
- Main must pass the exact current PlanningPacket attachment path to that analyst; the analyst may read only those attachment paths.
- `algoforge-main` need not read full incumbent source. The Harness supplies a
  bounded AST capability audit with symbols, control expressions, loops, and
  call edges. Main must not describe an audited existing mechanism as missing.
- The analyst subagents are read-only and do not use `bash` or `edit`.
- `algoforge-worker` must not replace Main's selected families. It may study and
  combine the allow-listed Worker Implementation Skills at code level; `task`,
  `question`, network, unselected Skills, and broad repository discovery remain denied.
- A repair revision must preserve direction id, method package, and target file.
- The Worker must not read the full Context Packet, method catalog, experience memory, or old attempts.

## Authoring guidance

- The backend remains algorithm-agnostic. Named algorithm behavior comes only
  from the selected knowledge/Skill/Method Package inputs.
- A method package is complete only when all required components and coupled
  groups have reachable behavior; names or unused helpers are not evidence.
- Preserve Core-valid incumbent behavior and let the fixed evaluator decide
  legality and objective improvement.
