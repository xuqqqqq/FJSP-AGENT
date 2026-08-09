# Machine-Availability FJSP Semantics And Decoder

Each maintenance record is a fixed half-open interval `[u_start,u_end)`. A non-preemptive operation `[s,e)` is legal only when `e <= u_start` or `s >= u_end` for every interval on its selected machine. Equality at either boundary is legal.

For search, sort and merge overlapping/touching windows into an equivalent union. Earliest-gap placement scans scheduled operations and blocked windows together. If an operation intersects a window, advance its candidate start to the end of that window and continue scanning; never split processing around maintenance.

The fixed evaluator checks the original interval list and reports violation count and total listed unavailable duration. Solver-side interval merging must not alter evaluator metrics or semantics.
