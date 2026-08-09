# Release-Time FJSP Semantics And Decoder

The variant is static FJSP with two additional lower-bound vectors. For job `j`, the first operation has `S[j,0] >= r[j]`. For every selected machine `m`, `S[j,o] >= a[m]`. These bounds do not alter candidate processing durations or machine capacity.

An active decoder initializes job-ready time from `r[j]` and machine calendar origin from `a[m]`. Gap insertion must clip every candidate gap to both bounds. A schedule that only delays the first operation after ordinary decoding is unsafe because it may create downstream machine overlap; propagate timing through the full precedence/resource graph.

The authoritative format is the supplied release-time IO document. Real entries are nonnegative and row padding is exactly `-1`. The fixed evaluator recomputes makespan and checks both vectors.
