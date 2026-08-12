# V2-003 Durable Thread Updates and Route Decisions

Status: branch implementation complete; stacked review pending

V2-003 closes the semantic gap between one shared chat surface and isolated,
concurrent TaskThreads. A routed update is no longer only an Inbox label: the route
decision and its Thread effect commit together, survive restart, and replay from the
event journal.

## Accepted semantics

| User meaning | Durable result |
| --- | --- |
| Supplement | Append `thread.message_appended`, retain the TaskSnapshot, compile a new ContextManifest, cancel the superseded non-terminal Run, and enqueue a replacement Run |
| Goal or acceptance change | Preserve the old TaskSnapshot, create a new TaskSnapshot and ContextManifest, cancel superseded work, and enqueue a new Run |
| Method change | Preserve the old branch and Run, append the update, fork and select a new Branch, freeze new snapshots, and enqueue a new Run |
| Cancel | Append the user message, set desired state to `CANCEL`, revoke pending Approvals and unexecuted Action Intents, cancel non-terminal Runs/jobs, fence old Worker leases, and move the Thread to `CANCELLED` |
| Ambiguous continuation | Keep the route `PROPOSED`; do not append to a Thread or enqueue a Run until the original actor decides |
| Ordinary chat | Append to the conversation stream without creating a TaskThread |

A terminal Run is never reopened, and a prior snapshot is never edited. New user
meaning creates new immutable execution inputs.

## Atomic route boundary

For a first-seen confirmed update, one SQLite transaction writes:

1. the inbound dedupe row and normalized message Artifact references;
2. `conversation.message_received` and the route proposal/confirmation events;
3. `thread.message_appended`;
4. cancellation events for superseded Run and scheduler records;
5. optional Branch fork/selection events;
6. the replacement Run, frozen snapshot bindings, and scheduler job.

For an ambiguous message, steps 3-6 are withheld. The later decision transaction
changes `PROPOSED` to `CORRECTED` or `EXPIRED`, writes the decision event, and applies
the selected Task or Thread effect exactly once. A crash rolls back both the decision
and its effect.

## Route decision surface

The text fallback is intentionally explicit while interactive cards remain deferred:

```text
/route <message-key> thread <thread-id> [supplement|goal|method|cancel]
/route <message-key> new
/route <message-key> dismiss
```

The loopback API exposes the same domain operation:

```text
POST /runtime/inbox/{message-key}/decision
```

Only the original `platform + actor_id` may resolve a proposed route. Local route
decision requests are loopback-only. Candidate Threads are derived from the same
platform, chat, and Project and exclude cancelled or archived work.

## Durable model additions

- route states: `PROPOSED`, `CONFIRMED`, `CORRECTED`, `EXPIRED`;
- update kinds: `SUPPLEMENT`, `GOAL_CHANGE`, `METHOD_CHANGE`, `CANCEL`;
- Branch projection with fork event, base projection hash, reason, and status;
- Thread update projection linked to message, Artifact, kind, Branch, snapshots, and
  replacement Run;
- Run creation time and `supersedes_run_id` lineage;
- `inbox.route_corrected`, `inbox.route_expired`, `thread.message_appended`,
  `thread.branch_forked`, `thread.branch_selected`, and
  `scheduler.run_cancelled` events.

All new Thread projection fields are reconstructed by the pure reducer and covered by
projection-hash replay tests.

## Concurrency and recovery

- Thread updates use the existing optimistic revision boundary.
- The Inbox message key remains the idempotency key across restart and channel retry.
- Conflicting concurrent decisions serialize through SQLite; exactly one target gets
  the update.
- Replacing a claimed Run marks its scheduler job `CANCELLED`, clears the lease, and
  makes the old Worker fail the next fenced heartbeat or write.
- Pending Approvals and not-yet-executed Action Intents owned by a superseded Run are
  cancelled in the same transaction, so an old approval cannot later invoke a Provider.
- Prepared CAS objects may remain unreferenced after a rolled-back contention attempt;
  they never become Runtime truth without the transaction.

## Branch acceptance

The V2-003 suite covers:

- supplement context recompilation without TaskSnapshot mutation;
- goal change with old/new TaskSnapshot comparison;
- method-change fork and selection with preserved old Run;
- cancellation across restart for Thread, Run, and scheduler state;
- cancellation of pending Approvals and unexecuted Action Intents;
- running-Worker fencing after an update;
- ambiguous route inactivity before decision;
- requester authorization, expiry, idempotent retry, and conflicting concurrent
  decisions;
- local API and Feishu text-command resolution;
- deterministic Thread replay after every update class.

## Explicitly deferred

- V2-004 owns the long-running Worker Host and real Source-to-Artifact executor.
- V2-006 owns atomic Delivery, Attention, Outbox, and scheduler finalization.
- Already-confirmed route compensation is not implemented as destructive reassignment;
  V2-009 must represent it as a new reviewed correction decision with visible prior
  effects.
- Interactive Feishu cards and candidate labels arrive with the decision UI.
- Cancelling a Thread fences Runtime work but does not pretend to undo a Provider side
  effect that already has an Intent/Receipt or reconciliation requirement.

The next ordered slice is V2-004: a bounded Worker Host and deterministic first
Source-to-Artifact executor.
