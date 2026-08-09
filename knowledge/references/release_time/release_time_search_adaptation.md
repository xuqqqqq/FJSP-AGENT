# Release-Time FJSP Search Adaptation

Release times make early machine capacity uneven. Useful constructive priorities combine operation criticality with release slack and candidate-machine availability. Multiple starts should vary tie-breaking among released jobs without scheduling unreleased jobs.

For local, tabu, or memetic search, retain assignment/order as the move state and fully re-decode with release bounds. Favor moves that fill unavoidable idle gaps before future releases, reduce late release bottlenecks, or move operations away from machines with large initial availability. An incremental delta is only a filter; accepted candidates require a complete legality pass.

For exact hybrids, release bounds are cheap constraints and can be included in full or trust-region models. Heuristics remain useful for incumbent generation; CP-SAT is optional rather than the default.

## Research Basis

The supplied corpus includes work on GEP reactive policies and MCTS for dynamic FJSP with job release dates. This card reuses their release-aware priority/search intuition only; the platform contract is the supplied static release-time problem, so no online arrival or rescheduling semantics are introduced.
