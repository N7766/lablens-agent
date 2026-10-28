# Parcel-sort replay acceptance note

This is an invented, synthetic local benchmark for evaluation only. It is not a real customer measurement or a production deployment record.

Run family: parcel-sort replay. The local harness replays 600 fabricated parcel jobs, with one worker and a fixed input sequence. Two builds, Larch-12 and Larch-14, are evaluated separately under this same protocol. Acceptance requires both a job-latency p95 of at most 80 milliseconds and a job failure rate of at most 0.5 percent. Lower latency is better. A build that misses either limit does not pass this local acceptance gate.

These measurements do not establish production capacity, real customer error rates, or statistical significance. No production launch decision is recorded here.
