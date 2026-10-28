# CedarBend paired benchmark — synthetic
Run group: cb-lab-21. Scenario: fixed local CPU laboratory benchmark. Date label: 2026-10-29 (synthetic).
Both cedar-base and spruce-lab used the same host, 180 offered requests per second, and 120 requests. Results do not establish service capacity or production reliability.
| run_id | p95_latency_ms | peak_memory_mib | correct_answers | evaluated_answers | timeout_count |
| cedar-base | 84 | 192 | 118 | 120 | 0 |
| spruce-lab | 62 | 248 | 112 | 120 | 0 |
The latency acceptance target is p95_latency_ms <= 70. The correctness gate is correct_answers >= 118 out of the same 120 evaluated answers. Peak memory must be <= 256 MiB.
A candidate recommendation requires all three gates. Lower latency alone is insufficient. No production deployment was evaluated.
