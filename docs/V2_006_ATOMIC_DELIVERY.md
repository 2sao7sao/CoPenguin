# V2-006 — Atomic Delivery Finalization

Status: implemented for the Alpha Source-to-Artifact workflow

## Transaction contract

After the registered verifier passes, one `BEGIN IMMEDIATE` SQLite transaction
commits all terminal surfaces:

1. `Delivery PREPARED` with version, source refs, primary/supporting Artifacts,
   verifier report, sensitivity, export policy, and allowed owner decisions;
2. `Run COMPLETED` and its verified output Artifact;
3. `Thread DELIVERED` and `Attention DELIVERY_READY`;
4. `scheduler job COMPLETED` with the same Worker fence;
5. one stable-idempotency `delivery_ready` Outbox item;
6. immutable causal events for Delivery, Thread, scheduler, and Outbox.

`POST /runtime/deliveries/{delivery_id}/present` records the separate
`PREPARED -> PRESENTED` boundary. Presenting a result is not acceptance.

## Failure injection

The acceptance suite installs a SQLite trigger that aborts the Outbox insert.
It verifies that Delivery, Run, Thread, Attention, scheduler completion, Outbox,
and all terminal events roll back together. The live claim remains recoverable,
and Thread replay still matches the stored projection.

## Remaining boundary

V2-006 creates the transactional Outbox intent. A channel dispatcher and send
Receipt are still required before outbound notification itself is crash-safe.
Delivery owner decisions and revision Runs remain V2-007.
