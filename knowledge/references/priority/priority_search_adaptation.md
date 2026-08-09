# Priority-Aware Search Adaptation

## Construction

Use priority status as one feature in ready-operation ranking, together with remaining work, insertion finish, machine load, and assignment regret. Run complementary starts with several priority-pressure levels, including a makespan-oriented start. Always evaluate the complete result lexicographically.

## Coupled local search

The useful secondary critical structure is the longest-completing priority job and machine arcs that delay it. Candidate moves include changing a priority operation's machine, reinserting it within a machine sequence, and moving a blocking normal operation when this lowers priority completion without increasing makespan. Full re-decoding is required because a local move can shift both objective values indirectly.

## Population and memetic search

Encode assignment and operation order with the same legality-preserving decoder used for standard FJSP. Select by lexicographic rank and retain structural diversity across machine assignments and order. Priority-biased crossover or mutation can focus on priority jobs, while ordinary mutations prevent premature convergence and protect the primary objective.

## Exact and hybrid search

For CP-SAT, first minimize makespan. Given a feasible or proven primary value `M`, add `makespan <= M` and minimize a variable bounding the completion of every priority job. Under a time limit, preserve the best known primary bound before entering phase two. A local exact repair may release priority-job operations and their blocking machine neighborhood while fixing the rest of the incumbent.

## Acceptance and evidence

Replace the incumbent only when the recomputed tuple is lexicographically smaller. Report method activation separately from quality: priority-aware dispatch counts, accepted priority-targeted moves, CP-SAT phase statuses, time spent, and the final objective tuple.
