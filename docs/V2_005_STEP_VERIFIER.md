# V2-005 — Replay-visible Steps and Decision Record Verifier

Status: implemented for the Alpha Source-to-Artifact workflow

## Durable Step contract

Each Worker attempt records a deterministic transform Step and verifier Step.
The SQLite projection and immutable event journal retain:

- Step ID, Run ID, ordinal, kind, attempt, and Provider key/version;
- input and output Artifact IDs;
- bounded execution metadata;
- created, started, output-recorded, succeeded, or failed events;
- error code and verifier result when applicable.

A retry creates a new attempt identity. A stale Worker cannot finish the prior
Step because every mutation checks the live Worker claim and fencing token.

## DecisionRecordVerifier v1

The deterministic verifier performs no model or remote call. It emits an
immutable verifier-result Artifact covering:

- schema and required sections;
- evidence SourceSnapshot identity;
- citation pointers and Artifact integrity;
- allowed-use and access-envelope presence;
- requester-only sensitivity and non-publishable posture;
- bound source revision freshness;
- at least one decision, action item, or open question.

All checks must pass. Success produces a new immutable verified record that
references the verifier-result Artifact; the draft is never edited. Failure
records a failed verifier Step and cannot prepare a Delivery.

## Acceptance evidence

Tests cover successful Step/event traces, a failed actionability check, absence
of Delivery on verification failure, and replay equivalence.
