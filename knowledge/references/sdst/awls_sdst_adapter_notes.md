# AWLS-SDST Adapter Notes

This card is for agent context, not for changing evaluator semantics.

## Source Pointers

- AWLS/FJSP local search: "An Effective Local Search Algorithm for Flexible Job Shop Scheduling Problem" describes adaptive weighting-based local search (AWLS), tabu search, and operation weights for FJSP.
- FJSP-SDST: Shen, Dauzère-Pérès, and Neufeld (2018), "Solving the flexible job shop scheduling problem with sequence-dependent setup times", develops disjunctive graph ideas and tabu neighborhoods for makespan minimization with SDST.
- FJSP with setup times: González, Vela, and Varela, "An Efficient Memetic Algorithm for the Flexible Job Shop with Setup Times", ICAPS 2013, is related to the HUdata setup benchmark family.
- NS4S IJCAI 2025 reports SDST-HUdata experiments on 20 instances with a 30 second FJSP-SDST cutoff.

## Execution-Mode Rule

- Platform reference validation may reuse `harness_agent.domains.io`, but generic orchestration must not import or execute the method implementation.
- A standalone agent-generated solver must implement the active IO-derived parser, setup query, and output writer inside the generated artifact. It must not import `harness_agent` or evaluator internals.
- Both modes must preserve the frozen evaluator semantics. Never create a second parser inside the backend itself.

## Agent-First Stage Order

1. Make AWLS time propagation setup-aware and evaluator-valid.
2. Then adapt same-machine N7/N8 and change-machine NK/RK/LK move scoring.
3. Then expose setup-aware features to `zi` evolution.
4. Only after those pass smoke tests should the agent run HUdata subset/full benchmarks.
