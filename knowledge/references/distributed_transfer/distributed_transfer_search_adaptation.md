# Distributed FJSP With Transfers Search Adaptation

## Representation And Decoding

The supplied Luo et al. reference uses three coupled vectors: operation sequence (OS), factory assignment (FA), and machine assignment (MA). A repository solver may use another representation, but it must preserve the same three decisions and decode them together. A factory or machine change can alter processing time, transfer delay, factory workload, energy, and the feasible machine sequence; never score such a move by changing one cached term alone.

Maintain an independent fully decoded incumbent. Candidate comparison must fully recompute transfer-weighted job readiness and `(factory,machine)` resource capacity. Structurally distinct FA/MA assignments matter because low-makespan basins can have materially different workload and energy values.

## Initialization

The paper's GLR population combines global, local, and random assignment selection. Its reported implementation uses a 60/30/10 split, but those percentages are a starting hypothesis rather than a repository constant. Preserve the mechanism:

- global seeds update factory and machine load while assigning operations;
- local seeds diversify decisions within a job or bounded subset;
- random seeds retain assignment diversity and must still use a legal decoder;
- include at least one transfer-aware greedy seed that compares processing completion, predecessor transfer, and factory-load pressure.

For a single-solution constructive lane, emulate the useful part of GLR with bounded multi-start rules rather than presenting one greedy dispatch as a memetic algorithm.

## Variation And Local Search

Population search is the literature-backed primary mechanism for these DFM instances. Use precedence-preserving OS crossover, option-valid FA/MA crossover or mutation, structural duplicate control, and bounded local improvement. The paper proposes inverse sequencing mutation (ISM) over a short OS segment and replacing-machine mutation (RMM) over MA positions.

Its critical-path local search contains three complementary mechanisms:

- `LSO_SP`: swap or reposition operations around critical blocks while preserving job precedence;
- `LSO_MPT`: move a critical operation to an eligible factory-machine option with lower processing time;
- `LSO_RTT`: choose an eligible option that reduces predecessor/successor transfer penalties.

Repository implementations must evaluate these moves through a complete decoder. Add same-factory machine changes, cross-factory relocation, paired moves that avoid paying a transfer penalty twice, and load-balancing moves when they are authorized by the selected method family.

## Objective Contract

The paper maintains a Pareto population with non-dominated sorting and crowding distance. The current fixed Core intentionally selects one result lexicographically by makespan, maximum factory workload, then total energy consumption. A Worker may maintain a Pareto archive internally for diversity, but it must emit one legal solution under the fixed lexicographic contract. Do not replace this order with a weighted sum, and do not claim that the platform reproduces the paper's Pareto evaluation protocol.

Exact repair remains optional. If used, restrict it to a bounded critical operation set or a tractable complete model, report actual solver evidence, and preserve the heuristic incumbent on timeout.

## Research Basis

Primary supplied reference: Luo et al., *An efficient memetic algorithm for distributed flexible job shop scheduling problem with transfers* (2020). Its DFM representation, GLR initialization, OS/FA/MA encoding, ISM/RMM variation, and LSO_SP/LSO_MPT/LSO_RTT neighborhoods motivate these mechanisms. The repository's fixed IO and evaluator remain authoritative for identifiers, constants, and promotion order.
