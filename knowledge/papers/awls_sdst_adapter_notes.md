# AWLS-SDST Adapter Notes

This card is for agent context, not for changing evaluator semantics.

## Source Pointers

- AWLS/FJSP local search: "An Effective Local Search Algorithm for Flexible Job Shop Scheduling Problem" describes adaptive weighting-based local search (AWLS), tabu search, and operation weights for FJSP.
- FJSP-SDST: Shen, Dauzère-Pérès, and Neufeld (2018), "Solving the flexible job shop scheduling problem with sequence-dependent setup times", develops disjunctive graph ideas and tabu neighborhoods for makespan minimization with SDST.
- FJSP with setup times: González, Vela, and Varela, "An Efficient Memetic Algorithm for the Flexible Job Shop with Setup Times", ICAPS 2013, is related to the HUdata setup benchmark family.
- NS4S IJCAI 2025 reports SDST-HUdata experiments on 20 instances with a 30 second FJSP-SDST cutoff.

## Project-Specific Rule

The project already has canonical SDST parsing and validation in `harness_agent.domains.io`.
Workers must reuse:

- `parse_standard_fjsp`
- `setup_time_between`
- `validate_standard_schedule`

Do not create a second setup parser in examples or solver code.

## Agent-First Stage Order

1. Make AWLS time propagation setup-aware and evaluator-valid.
2. Then adapt same-machine N7/N8 and change-machine NK/RK/LK move scoring.
3. Then expose setup-aware features to `zi` evolution.
4. Only after those pass smoke tests should the agent run HUdata subset/full benchmarks.
