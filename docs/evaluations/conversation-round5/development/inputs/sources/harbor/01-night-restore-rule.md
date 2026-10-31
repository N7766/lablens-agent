# Harbor archive: Night Restore R-17 acceptance rule
Document status: approved. Scope: Night Restore R-17 only.
This is an invented archive and invented equipment in a synthetic local evaluation. Nothing here is a public-source fact, operational incident, or production benchmark.
R-17 acceptance requires BOTH: median restore latency strictly below 150 seconds, and successful restores divided by attempted restores of at least 99 percent. A median of exactly 150 seconds fails. Compare the unrounded success fraction against the threshold; a display rounded to 99 percent does not prove compliance.
These conditions apply to the recorded nightly restore workload. They do not establish behavior during power failures.
