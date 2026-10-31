# Round4 runtime transport controls

Actual JVM → actual Python Agent → scripted local provider. No external model/embedding requests.

The provider returns exactly two decisions (search, verify/report) followed by a review. Its token counts are synthetic. This is transport and durability evidence, not model quality, inference performance, or load testing.

Status: passed; checks: 64/64.

| Case | Passed |
| --- | --- |
| fast_success | True |
| review_total_timeout | True |

A 6-second Agent budget retains a 2-second review reserve. The timeout fixture delays only the review response by 8 seconds. Timing assertions concern pairing, ordering and recorded budgets; no machine-specific minimum latency is required.

Evidence: [summary](summary.json), [raw HTTP observations](http-observations.json), [raw scripted-provider events](provider-events.json). Sources and the executed immutable JAR are fingerprinted. Main and parent QA ports are protected. The isolated database is retained; owned processes are stopped.

The client correctly records unknown review acceptance/usage after timeout even though this controlled provider independently records receiving the request. Nested stages and request durations must not be summed.
