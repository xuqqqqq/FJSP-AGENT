# Distributed FJSP With Transfers Semantics And Decoder

Each operation selects a `(factory,machine)` candidate with processing duration and unit energy. Adjacent job operations require delay 0 on the same machine, 30 on different machines in one factory, or 60 across factories. Resource capacity is scoped by the full pair, not machine id alone.

Input machine IDs are global 1-based identifiers bounded by `1..F*M`, but their numeric value does not determine the factory; the explicit factory marker that starts each candidate group is authoritative. Output factory and machine IDs are both decremented to 0-based, while resource identity remains the full `(factory,machine)` pair. Never renumber machines within a factory or infer factory boundaries from machine values. Processing energy is `duration * unit_energy`; transfer energy is `transfer_delay * 6`. Factory workload is the sum of selected processing durations in each factory. The fixed evaluator compares makespan, maximum factory workload, then total energy consumption lexicographically.

A legal decoder propagates transfer-weighted job arcs and factory-machine resource arcs. Every accepted candidate is revalidated for coverage, candidate membership, duration, transfer precedence, and pair-scoped overlap.
