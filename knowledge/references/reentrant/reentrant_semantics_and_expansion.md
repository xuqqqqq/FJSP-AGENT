---
id: fjsp-reentrant-semantics-and-expansion
type: reference
title: Re-entrant FJSP Tail Semantics And Expansion
tags: [fjsp, reentrant_route, loop_expansion, parser, evaluator]
status: active
---

# Re-entrant FJSP Semantics And Expansion

The active benchmark is a deliberately bounded Re-entrant FJSP contract. A standard FJSP body is followed by exactly one contiguous loop triple for each job: `(loop_start, loop_end, repeat)`. The route becomes `pre + body * repeat + post`. There are no extra resource or timing constraints after expansion.

Each expanded visit is a distinct operation with a continuous 0-based `op_id`. It inherits the source operation's candidate machines and durations, but different passes choose machines independently. The evaluator therefore remains the standard precedence, eligibility, duration, and machine-capacity validator over the expanded operation set.

This distinction matters operationally. Ignoring the tail can produce a schedule that appears legal for the unexpanded prefix while omitting 64-88 percent added work in the supplied Barnes-derived set. A safe parser validates every loop boundary and repeat, consumes all tail tokens, expands before constructing any search state, and checks output coverage against the expanded identities.

The supplied requirement/IO documents and `reentrant_fjsp_manifest.json` are authoritative for this encoding. Broader re-entrant literature may use arbitrary cyclic routes, multiple loops, batching, rework probabilities, release control, or parallel work centers; none of those semantics should be inferred here.
