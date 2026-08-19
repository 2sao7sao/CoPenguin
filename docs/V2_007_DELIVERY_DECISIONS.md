# V2-007 — Replayable Delivery Decisions and Immutable Revision Runs

Status: implemented for the Alpha Source-to-Artifact workflow

## Owner-decision contract

A `Delivery` must cross the explicit `PREPARED -> PRESENTED` boundary before it
can receive one owner decision:

```text
PRESENTED -> ACCEPTED
          -> REVISION_REQUESTED
          -> REJECTED
          -> DEFERRED
          -> TAKEN_OVER
```

Every decision has a stable idempotency key, immutable decision-evidence
Artifact, actor, event, timestamp, and `DeliveryDecisionRecord`. Reusing the
same key with different evidence fails instead of silently changing the prior
decision. The Delivery event stream has its own pure reducer and its stored
projection hash can be checked against replay.

Accepting a Delivery is only a product decision. It does not publish to
Feishu, promote EvolveMemory or EvolveKB content, or expand a permission.
Rejecting and deferring also produce no downstream promotion. Taking over
clears Delivery attention and changes the Thread desired state to `PAUSE`, so a
later queued Run cannot begin without an explicit resume.

## Revision transaction

`REVISION_REQUESTED` stores the requested change in an immutable Artifact,
then compiles a new TaskSnapshot and ContextManifest. One SQLite transaction
records the decision and:

1. creates a new Run linked with `supersedes_run_id`;
2. binds the new Task, prior Agent, and new Context snapshots;
3. transitions the Run and Thread to `QUEUED`;
4. clears the old `DELIVERY_READY` Attention item;
5. inserts the scheduler job and enqueue event; and
6. records `delivery.revision_run_created`.

The prior Run, Delivery, verifier result, and Artifact remain immutable. When
the revision Run completes, normal V2-006 finalization creates Delivery `vN+1`
with `previous_delivery_id` pointing to the rejected version. A scheduler
insert failure is injected in the acceptance suite to prove that the decision,
new Run, Thread state, scheduler job, and events roll back together.

## Local and Feishu surfaces

The loopback API exposes:

```text
POST /runtime/deliveries/{delivery_id}/decision
```

The request must provide `decision`, `actor_id`, and `idempotency_key`; `revise`
also requires `revision_request`. The response includes the resulting Delivery,
decision record, optional revision job, Thread projection, and replay checks.

The Feishu card parser recognizes `copenguin.delivery.v1`, binds every button
to one Delivery ID, requires bounded form input for revision, journals the card
callback through unified Ingress, and calls the same decision service. The card
schema contains `Accept`, `Revise`, `Reject`, `Later`, and `Take over` actions.

## Acceptance evidence

Tests cover:

- all five owner decisions and Attention convergence;
- idempotent retries and conflicting idempotency evidence;
- the required presentation boundary;
- immutable revision snapshots and `supersedes_run_id`;
- revision Run execution into Delivery `v2`;
- injected rollback between decision and scheduler insert;
- Thread and Delivery replay equivalence;
- loopback API decisions; and
- signed Feishu card parsing, duplicate callbacks, and durable decisions.

## Remaining trust boundary

The Feishu card and callback path is credential-free and locally verified. A
real channel dispatcher, send Receipt, actor-scoped Control Room authentication,
durable publish Approval, Wiki Provider, and credential-backed smoke test remain
separate work. Until those exist, CoPenguin must not claim that an accepted
Delivery was sent or published externally.
