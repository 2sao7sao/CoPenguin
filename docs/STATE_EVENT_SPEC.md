# State and Event Protocol v0.1

This protocol is the durable boundary between the CoPenguin control plane and
ephemeral runtime workers. A Thread is persistent; a worker process is not.

## Aggregate hierarchy

```text
Project -> TaskThread -> Branch -> Run -> Step -> Artifact/Delivery
```

- `Project` scopes long-lived resources and policy.
- `TaskThread` is the stable goal and sidebar identity.
- `Branch` represents an alternative method, not another copy of the chat.
- `Run` is one execution attempt.
- `Step` is an individual model/tool/verification operation.
- `Delivery` is a versioned result linked to artifacts and evidence.

The first implementation keeps Thread and its main Runs in one aggregate. This
gives the Thread a single writer while allowing worker jobs from different
Threads to run concurrently.

## Event envelope

Every durable mutation appends an immutable event containing:

| Field | Meaning |
| --- | --- |
| `event_id` | Global idempotency identity. |
| `global_position` | Total journal order used by projections and observers. |
| `stream_type`, `stream_id` | Per-aggregate ordered stream. |
| `sequence` | Monotonic version inside one stream. |
| `project_id`, `thread_id`, `branch_id`, `run_id` | Navigation and filtering identities. |
| `correlation_id` | Events belonging to the same user request or activity. |
| `causation_id` | The event or intent that caused this event. |
| `event_type` | Versioned semantic name. |
| `actor` | User, coordinator, worker, hook, or system component. |
| `occurred_at` | UTC timestamp recorded in the event. |
| `payload` | Canonical JSON data; no process-local objects. |
| `schema_version` | Payload migration version. |

Streams are append-only. Reusing an `event_id` with identical content is an
idempotent retry; reusing it with different content is an error.

## Thread state

Thread state is separated into three orthogonal values.

### Desired state

```text
RUN | PAUSE | CANCEL | ARCHIVE
```

This records what the user or scheduler wants. It does not claim that the
runtime has already reached that state.

### Actual state

```text
CREATED
DORMANT
QUEUED
RUNNING
WAITING_USER
WAITING_APPROVAL
WAITING_RECEIPT
WAITING_DEPENDENCY
WAITING_RESOURCE
VERIFYING
DELIVERED
FAILED
PAUSED
CANCELLED
ARCHIVED
```

### Attention state

```text
NONE
NEEDS_INPUT
NEEDS_APPROVAL
HAS_CONFLICT
DELIVERY_READY
FAILED
```

Attention is a sidebar/user concept and must not be inferred only from worker
process status.

## Run state

```text
CREATED -> QUEUED -> RUNNING -> VERIFYING -> COMPLETED
                         |          |       -> PARTIAL
                         |          |       -> FAILED
                         |          |       -> QUARANTINED
                         |          |       -> CANCELLED
                         |
                         +-> WAITING_USER ---------> RUNNING
                         +-> WAITING_APPROVAL -----> RUNNING
                         +-> WAITING_RECEIPT ------> VERIFYING
                         +-> WAITING_DEPENDENCY ---> RUNNING
                         +-> WAITING_RESOURCE -----> RUNNING
```

`COMPLETED`, `PARTIAL`, `FAILED`, `QUARANTINED`, and `CANCELLED` are terminal.
A retry creates a new Run and links it to the previous attempt; it does not
reopen a terminal Run.

## Current event types

Thread aggregate:

- `thread.created`
- `thread.state_changed`
- `thread.desired_state_changed`
- `thread.attention_changed`
- `run.created`
- `run.state_changed`
- `run.snapshots_bound`
- `run.checkpoint_recorded`
- `delivery.recorded`

Scheduler stream:

- `scheduler.run_enqueued`
- `scheduler.run_claimed`
- `scheduler.run_reclaimed`
- `scheduler.run_heartbeat`
- `scheduler.run_retry_scheduled`
- `scheduler.run_completed`
- `scheduler.run_failed`

Resource stream:

- `resource.lease_acquired`
- `resource.lease_renewed`
- `resource.lease_released`
- `resource.lease_expired`

Action stream:

- `action.intent_created`
- `action.execution_claimed`
- `action.reconciliation_required`
- `action.reconciliation_claimed`
- `action.receipt_recorded`

Conversation and Inbox streams:

- `conversation.message_received`
- `inbox.route_proposed`
- `inbox.route_confirmed`

`inbox.message_routed` is the v0.1 event name and is no longer emitted by the
V2 unified ingress path.

Approval stream:

- `approval.requested`
- `approval.approved`
- `approval.denied`
- `approval.expired`

Delivery stream:

- `delivery.prepared`
- `delivery.presented`
- `delivery.decision_recorded`
- `delivery.revision_run_created`
- `delivery.notification_enqueued`

The Delivery projection has an independent pure reducer. A decision requires a
presented Delivery and can occur once; revision preserves the prior version and
atomically creates a new snapshot-bound Run.

## Snapshot binding

Before a Run starts, `run.snapshots_bound` freezes three Artifact CAS references:

- `TaskSnapshot`: objective, domain, constraints, acceptance criteria, and input
  artifact identities;
- `AgentSnapshot`: model profile, tool/capability registry, permissions, hooks,
  and EvolveMemory/EvolveKB snapshot references;
- `ContextManifest`: exact ordered context items, allowed-use decisions,
  sensitivity metadata, source references, and compiler version.

The reducer rejects rebinding and rejects first-time binding after execution has
started. Replay therefore identifies the exact task, Agent configuration, and
context used by the original Run.

## Action state and reconciliation

```text
PENDING -> EXECUTING -> SUCCEEDED
                    -> FAILED
                    -> RECONCILE_REQUIRED

RECONCILE_REQUIRED -> RECOVERING -> SUCCEEDED
                                -> FAILED
                                -> PENDING (provider confirms not found)
                                -> RECONCILE_REQUIRED (still unknown)
```

An Intent stores only the immutable request artifact identity and its matching
payload hash. The provider must receive the Intent idempotency key. An expired
execution lease is evidence of uncertainty, not evidence that the side effect
failed, so normal execution claims are blocked until reconciliation completes.

An Intent marked `requires_approval` cannot receive a normal execution claim
until its persistent Approval is `APPROVED`. A denied or expired Approval
cancels that Intent. Reconciliation claims are allowed because they query the
provider for an already-possible side effect rather than authorizing a new one.
While any Approval for a Thread is pending, its attention projection is
`NEEDS_APPROVAL`; this is independent of the worker process.

## Concurrency invariants

1. A Thread projection is written with `expected_revision`.
2. Only one caller can successfully append from a given revision.
3. Different Thread streams may be written and executed concurrently.
4. Only one claimed main Run per Thread is scheduled at a time.
5. Worker claims expire and are fenced by a monotonically increasing token.
6. A stale worker cannot heartbeat or finish after a newer claim exists.
7. Resource reads may share a lease; write/exclusive claims conflict with all
   other active claims.
8. Expired resource owners cannot renew, release, or commit with an old token.
9. Projection state is never accepted as the source of truth; it can be rebuilt
   from events and verified by its canonical SHA-256 hash.
10. A claimed worker may record a checkpoint only while its scheduler lease and
    fencing token are current.
11. A Run cannot start before Task, Agent, and Context snapshot references are
    bound.
12. A Delivery decision is idempotent, replayable, and cannot mutate the
    completed Run or replace the prior Delivery Artifact.

## Multi-link, shared-history model

Conversation, execution, decision, artifact, and governance histories are
logical views over one event journal. They are connected by stable aggregate
ids plus `correlation_id` and `causation_id`. A UI can therefore show a simple
conversation timeline while an audit view follows the exact causal chain.

Branch events include `forked_from_event_id` and `base_snapshot_hash`; Delivery
revision events link the new Run to the prior Delivery and Run. Artifact events
use immutable CAS references.

## Crash recovery rule

Workers own leases, never durable truth. After a crash:

1. The scheduler waits for the old claim to expire.
2. A new worker reclaims the Run and receives a larger fencing token.
3. The worker loads the latest Thread projection or replays its event stream.
4. Expired action claims become `RECONCILE_REQUIRED`; the next worker must ask
   the provider about the existing idempotency key before any retry.
5. It resumes from the last verified checkpoint.

Checkpoint orchestration and persisted Approval binding are implemented. A
checkpoint is an immutable Artifact CAS object; `run.checkpoint_recorded`
stores only its reference and the next strict sequence number. The reclaimed
worker validates that the checkpoint belongs to the claimed Thread and Run.

The remaining P1.5 recovery work is the full Step/verifier/Delivery lifecycle
and atomic coordination of terminal Run state with scheduler completion.
