# Bounded conversation summary

Round 18 uses an **extractive user-history summary**. It copies earlier user
questions exactly, with turn sequence, original snapshot ID and SHA-256 hashes.
It removes the corresponding assistant answers. It does not ask an LLM to
rewrite history or decide which old correction, negation or unfinished request
is unimportant.

## Persistence and limits

The JVM keeps the most recent 20 terminal turns in each immutable task snapshot.
Earlier user questions are stored in `conversation_context.summary`. A summary
contains at most 100 messages and 16 KiB of canonical UTF-8 JSON. Queries for older
history are bounded, and admission also checks the final-review shape: the same
summary plus user questions from all but the latest two full turns.

If exact history cannot fit, or expected historical rows are missing, admission
returns HTTP 409 with `context_history_requires_new_conversation`. It creates no
new task, conversation revision, state update or outbox event. The user must
start a new conversation and explicitly restate the goal, constraints and
unresolved questions. These limits deliberately trade conversation length for
faithful preservation; this is not unlimited memory or semantic compression.

Python validates the schema, chronological order, each question hash, aggregate
hash and byte limit. Its context budget may remove complete old assistant/user
turn pairs, but the exact removed user messages remain in the summary. If either
the summary or required model context is too large, execution ends before the
next provider call. Final review receives the same attributed user context.

The current question and its explicit corrections take precedence. Summary text
is user context, not system instructions, verified facts or source passages. It
is never added to the retrieval corpus or citation catalogue. Old snapshots
without summaries remain readable, but `unavailable_legacy_user_turns` explicitly
records the history that was already omitted; the implementation cannot recover
it from a frozen snapshot by guessing.

## Format and verification

`conversation-summary-v1` contains `messages`, `source_range`, `source_sha256`,
`omitted_assistant_answers` and authority
`exact_previous_user_messages_not_verified_evidence`. Each message contains
`seq`, `question`, `snapshot_id` and `question_sha256`. Aggregate hashing uses the
compact, recursively sorted-key JSON encoding of `messages`, UTF-8 text and
lowercase control-character escapes. Hashes detect mismatched content; they are
not cryptographic attestations that a user's statement is true.

Tests cover Java/Python hash equality for Chinese, emoji and escaped controls;
exact preservation of old corrections, negations and unresolved requests;
immutable snapshot round trips; tampering and capacity rejection; old user text
being rejected as a citation; and real MySQL admission rollback. Scripted model
tests establish protocol behavior, not general model answer quality.

One regression exposed a necessary budget change in the test fixture: keeping
18 exact older user questions as well as two large recent answers no longer fit
the fixture's old 27,000-unit allowance. That fixture now provides 32,000 units
while still forcing old-answer eviction. Production context limits were not
raised. Another edge was caught before delivery: checking only history older
than 20 turns could accept a task that immediately exceeded the final-review
summary limit. Admission now checks that eventual review shape before mutating
durable task or conversation state.
