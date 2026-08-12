# CoPenguin V2 Runtime Contract

Status: implementation proposal under the accepted V2 product direction

Accepted product theme: Trusted Closure

Accepted Alpha Golden Path: Source to Inspectable Artifact

This document turns the accepted V2 direction into proposed implementation
constraints. The implementation order and individual contracts remain
reviewable. It does
not authorize autonomous promotion or broaden the current side-effect policy.

## 1. Required invariants

1. Every accepted inbound message has one durable identity before processing.
2. A message may become conversation, a new TaskThread, a Thread update, a
   control action, or a confirmation request. It is never silently all of them.
3. Every Run is bound to immutable Task, Agent, Context, Policy, Skill, KB, and
   Hook snapshots before the first Step.
4. A Step is the smallest replay-visible model, tool, or verifier operation.
5. A scheduler job cannot become terminal independently from its Run.
6. A completed Run has exactly one prepared Delivery; a failed or quarantined
   Run has a terminal failure record.
7. A Delivery decision never mutates the original Delivery. Revision creates a
   new Run and a new Delivery version.
8. An external side effect requires Intent, a live fenced claim, Provider
   idempotency, and Receipt or explicit reconciliation state.
9. Hooks can advise, veto, propose, or observe. They cannot directly mutate
   durable state or call external Providers.
10. Observers can open ReviewCases and remediation proposals. They cannot
    promote Memory, KB, Skill, Hook, model, or permission snapshots.

## 2. Runtime component boundaries

### IngressAdapter

Responsibilities:

- normalize Feishu, local UI, and CLI input;
- verify channel authentication and actor identity;
- assign `message_key = platform:message_id`;
- persist or return the prior idempotent result;
- enqueue outbound responses through the Outbox.

It does not decide whether a message is a Task and does not call a Provider.

### RouteDecisionService

Inputs:

- normalized message Artifact;
- current Project and focused Thread;
- active Thread candidates;
- versioned router policy/model snapshot.

Outputs:

- route type;
- confidence and reason codes;
- proposed target Thread/Branch;
- confirmation requirement;
- route decision snapshot.

Any semantic model output is advisory until the deterministic policy and
confirmation threshold are applied.

### TaskService

Responsibilities:

- create TaskThread, Branch, Run, and TaskSnapshot;
- append user updates to an existing Thread;
- preserve correction and branch causation;
- apply desired-state commands;
- never reopen a terminal Run.

### SnapshotCompiler

Produces the immutable execution manifest:

```text
ExecutionManifest
  task_snapshot_id
  agent_snapshot_id
  context_manifest_id
  memory_policy_snapshot_id
  kb_snapshot_id
  skill_registry_snapshot_id
  hook_registry_snapshot_id
  tool_permission_snapshot_id
  verifier_registry_snapshot_id
  model_provider_snapshot_id
```

Missing optional layers must be represented explicitly, not inferred from the
current process configuration during replay.

### WorkerHost

The WorkerHost owns leases, not durable truth. It must:

- claim one runnable job;
- start or recover its Run;
- maintain heartbeat and cancellation state;
- load only the bound execution manifest;
- invoke the StepEngine;
- checkpoint verified progress;
- call `finalize_run` or record a classified failure;
- release all resource leases on a normal exit.

The first implementation supports one process with configurable worker
concurrency. Multi-process correctness remains testable through SQLite fencing.

### StepEngine

Each Step has:

- `step_id`, `run_id`, `ordinal`, `kind`;
- input Artifact IDs;
- output Artifact IDs;
- capability and Provider identity;
- model/tool configuration snapshot;
- budget and timeout;
- state and attempt;
- causation IDs;
- verifier result when required.

Kinds in V2:

- `model`;
- `tool_read`;
- `tool_write`;
- `transform`;
- `verifier`;
- `delivery_prepare`.

### CapabilityGateway

All tool calls pass through one policy boundary. Read-only calls still produce
Step Artifacts and trace events. Writes additionally require Action Intent and
Receipt. Risk is a policy decision based on capability, arguments, domain,
resource, actor, and current permission snapshot—not regex alone.

Before execution it must also resolve an explicit approver policy. The Approval
domain store records decisions; an authorization service determines whether the
actor may decide that exact capability, Project, Thread, risk level, and scope.

### DeliveryService

Prepares a versioned Delivery containing:

- summary Artifact;
- primary and supporting Artifact IDs;
- source/evidence references;
- verifier verdict and report;
- major decision records;
- diff from the prior Delivery;
- permitted user decisions;
- external Receipt references;
- sensitivity and export policy.

### OutboxDispatcher

Notification intent is written in the same transaction as the state that needs
notification. Dispatch is retried with a stable idempotency key. Channel send
success becomes a Receipt; a send failure does not roll back Runtime truth.

### ObservationMonitor

Reads events after a stored cursor and emits normalized observations. A detector
has a version, evidence query, threshold, cooldown, severity, dedupe key, and
allowed response types. Its write authority is limited to observation and
ReviewCase streams.

## 3. New and revised durable entities

### RouteDecision

```text
PROPOSED -> CONFIRMED
         -> CORRECTED
         -> EXPIRED
```

A high-confidence reversible route may be auto-confirmed by policy. Ambiguous
continuations stay PROPOSED and cannot enqueue a Run.

### Branch

Fields:

- `branch_id`;
- `thread_id`;
- `forked_from_event_id`;
- `base_snapshot_hash`;
- `reason_code`;
- `status = ACTIVE | SELECTED | REJECTED | MERGED`;
- `created_by` and timestamps.

### Step

```text
CREATED -> RUNNING -> SUCCEEDED
                   -> FAILED
                   -> WAITING_APPROVAL
                   -> WAITING_INPUT
                   -> WAITING_RESOURCE
                   -> QUARANTINED
                   -> CANCELLED
```

Waiting states resume the same Step only when execution identity is preserved.
A retry after a terminal Step creates a new attempt record.

### Delivery

```text
PREPARED -> PRESENTED -> ACCEPTED
                      -> REVISION_REQUESTED
                      -> REJECTED
                      -> DEFERRED
                      -> TAKEN_OVER
```

Acceptance is a user/product decision and does not alter the completed Run.

### ReviewCase

```text
OPEN -> ACKNOWLEDGED -> REMEDIATION_PROPOSED -> RESOLVED
                                      \-----> DISMISSED
```

Every case has evidence event IDs, detector version, severity, owner, cooldown,
and permitted remediation class.

## 4. Event additions

### Conversation and routing

- `conversation.message_received`
- `conversation.message_appended`
- `inbox.route_proposed`
- `inbox.route_confirmed`
- `inbox.route_corrected`
- `inbox.route_expired`
- `thread.message_appended`
- `thread.branch_forked`
- `thread.branch_selected`
- `thread.branch_rejected`

### Step and execution

- `step.created`
- `step.started`
- `step.waiting`
- `step.output_recorded`
- `step.succeeded`
- `step.failed`
- `step.quarantined`
- `step.cancelled`
- `run.budget_updated`
- `run.cancellation_observed`

### Delivery and decision

- `delivery.prepared`
- `delivery.presented`
- `delivery.decision_recorded`
- `delivery.revision_run_created`
- `delivery.notification_enqueued`
- `delivery.notification_receipted`

The existing `delivery.recorded` event requires a v1-to-v2 upcaster or an
explicit deprecation migration. It must not silently change meaning.

### Hooks and observations

- `hook.invocation_started`
- `hook.advice_emitted`
- `hook.vetoed`
- `hook.invocation_finished`
- `hook.invocation_failed`
- `observation.detected`
- `review_case.opened`
- `review_case.acknowledged`
- `remediation.proposed`
- `review_case.resolved`

## 5. Atomic transaction contracts

### Accept inbound message

One transaction writes:

1. inbox dedupe record;
2. message Artifact reference;
3. route proposal/decision;
4. Task submission or Thread update when confirmed;
5. response outbox item.

### Finalize successful Run

One transaction writes:

1. final verifier result;
2. Delivery PREPARED;
3. Run COMPLETED/PARTIAL;
4. Thread DELIVERED;
5. Attention DELIVERY_READY;
6. scheduler job COMPLETED;
7. notification outbox item;
8. resource-release intents or durable cleanup record.

### Finalize failed Run

One transaction writes:

1. classified failure and evidence;
2. retry schedule or terminal Run failure;
3. scheduler state;
4. Thread and Attention state;
5. notification outbox item when user action is needed.

### Decide Delivery

One transaction writes the user decision. `REVISION_REQUESTED` also creates a
new Run linked to the prior Delivery and freezes a new TaskSnapshot containing
the requested change. It does not edit the old snapshots.

## 6. Hook contract

```json
{
  "hook_id": "policy.pre_action.default",
  "version": 3,
  "phase": "pre_action",
  "priority": 100,
  "input_schema": "artifact:sha256:...",
  "output_schema": "artifact:sha256:...",
  "timeout_ms": 500,
  "failure_policy": "closed",
  "capabilities": ["runtime.read.intent"],
  "sensitivity_ceiling": "confidential",
  "deterministic": true,
  "source_artifact_id": "artifact:sha256:..."
}
```

Failure policies:

- `open`: record failure and continue; allowed only for non-safety advice;
- `closed`: block the phase and request review;
- `quarantine`: stop the Run and open a high-severity ReviewCase.

Hook output is schema-validated, size-limited, time-limited, and stored as an
Artifact. The Runtime translates a valid proposal into events or Intents; the
Hook never holds a database connection or Provider credential.

## 7. Self-loop detector contract

```json
{
  "detector_id": "repeated-route-correction",
  "version": 1,
  "window": {"events": 50, "duration": "P14D"},
  "threshold": 3,
  "dedupe_key": "project+route_signature",
  "cooldown": "P7D",
  "severity": "medium",
  "response": "open_review_case"
}
```

Required detector properties:

- evidence events remain accessible;
- observed and inferred signals are distinct;
- no detector consumes raw secrets by default;
- reprocessing the same cursor range is idempotent;
- a detector cannot assign product validation outcomes;
- dismissal is recorded and suppresses duplicate cases during cooldown.

## 8. Memory context contract

The Runtime receives a gated `MemorySelectionManifest`, not arbitrary memory
records:

```text
selection_id
memory_policy_snapshot_id
query_scope
selected items:
  memory_id
  version
  kind
  subject
  scope
  allowed_use
  sensitivity
  provenance_ref
  decision
compiler_version
```

Only `user_confirmed` items may be injected for direct factual use. An
`inferred_candidate` can be used only to ask a neutral confirmation question or
to appear in Memory Review; it cannot alter a Task or external action.

## 9. Security and privacy requirements

- Runtime/Control Room APIs bind to loopback by default and require a local
  session token.
- Remote channel startup fails closed when callback verification, actor policy,
  or required credentials are missing.
- Channel credentials never enter Artifact CAS or event payloads.
- Artifact access checks actor, Project, sensitivity, and requested operation.
- Every bound Artifact is verified to exist, match its hash, and satisfy its
  declared schema before a Run starts.
- Provider credentials are passed through a credential boundary, not snapshots.
- Action policy is capability-scoped and argument-aware.
- Approval authorization is checked separately from storing the decision.
- Every outbound external write carries a stable idempotency key.
- Pilot evidence is stored separately from Runtime truth.
- Export and deletion paths are tested before a Pilot participant is enrolled.
- Logs default to identifiers and reason codes, not raw message content.
- Memory, KB, Provider, and channel adapters report explicit `ready`, `degraded`,
  or `disabled` capability state; silent No-op fallback is not allowed in a Run.

## 10. Recovery and test matrix

The V2 suite must inject a crash after each boundary:

1. inbound receipt before/after dedupe commit;
2. route confirmation before/after Task submission;
3. worker claim before/after Run start;
4. Step start before/after output Artifact;
5. Action provider call before/after Receipt;
6. verifier result before/after Delivery prepare;
7. Run finalize before/after Outbox dispatch;
8. Delivery revision decision before/after new Run enqueue;
9. Hook start before output/timeout/failure;
10. Observer cursor advance before/after ReviewCase commit.

For every point, assert:

- replay hash consistency;
- no duplicate side effect;
- no missing or duplicated Delivery;
- stale workers are fenced;
- Attention eventually converges;
- the user can inspect what happened.

## 11. Operability budgets

Initial local targets, to be benchmarked rather than assumed:

- inbox commit p95 under 150 ms excluding model routing;
- projection list p95 under 100 ms at 10,000 dormant Threads;
- worker crash recovery begins within two lease periods;
- Route/Delivery decisions remain available after restart;
- replay audit supports incremental cursors and does not block normal startup;
- every Run has configurable model cost, wall-clock, Step, and external-action
  budgets.

## 12. Evolution gate after V2

V2 may create candidates and run independent evaluation or shadow execution.
Promotion remains unavailable until all of these are true:

1. a selected workflow passes the product Pilot gate;
2. candidate evaluation uses held-out tasks and a pinned evaluator snapshot;
3. shadow results show no safety regression;
4. a human approves the exact candidate and rollout scope;
5. the previous pointer is retained for rollback;
6. post-promotion monitoring has explicit rollback triggers;
7. no component can promote its own implementation or evaluator.

This keeps self-improvement inside governance rather than turning governance
into another self-improving component.
