# V2-004 — Bounded Worker Host and Source-to-Artifact Executor

Status: implemented and locally test-backed in the convergence branch

## Scope

V2-004 adds a bounded Worker Host, Executor routing, heartbeat and lease fencing,
checkpoint recovery, classified retries, and one deterministic
`source_to_project_decision_record_v1` Executor. The Executor reads only frozen
Artifact CAS inputs and performs no network read, model call, publication,
memory promotion, or KB promotion.

## Acceptance evidence

- workers claim only jobs for registered Executor keys;
- concurrency is bounded from 1 to 32;
- Worker claims heartbeat and stale fencing tokens cannot settle a Run;
- checkpoints bind Executor key/version and survive restart;
- corrupt, unauthorized, over-budget, or missing source inputs fail closed;
- the output is an immutable non-publishable Project Decision Record draft;
- CLI commands queue a source task, run a bounded worker, and inspect Artifacts.

The original V2-004 compatibility mode closes a successful Run and returns its
Thread to `DORMANT`. When the V2-005 verifier is registered, the same Worker
continues into the atomic V2-006 Delivery path instead.

## Explicit non-goals

- no general planner or open-ended agent loop;
- no remote source capture;
- no Delivery decision or publication;
- no automatic learning or permission expansion.
