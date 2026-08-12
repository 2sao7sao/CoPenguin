# V2-002 — Durable Product Approvals

Status: implemented and test-backed on the V2-002 branch

## Outcome

The Feishu and local compatibility assistant no longer owns an in-memory approval
queue. A `/computer` command that reaches the product path now uses the same durable
safety boundary as the Runtime:

```text
durable Inbox record
  -> immutable computer-action request Artifact
  -> Action Intent
  -> versioned requester-only Approval policy
  -> durable Approval
  -> fenced Action claim
  -> ComputerProvider
  -> immutable observation Artifact
  -> Action Receipt
```

`/approve` and `/deny` are themselves durable CONTROL messages. The decision is
bound to an immutable evidence Artifact containing the actor, message identity,
decision, Approval ID, and policy snapshot reference. A direct call that bypasses
the Inbox fails closed.

## Authorization policy

The first policy is intentionally narrow:

- capability: `computer.execute`;
- approver: the same channel-qualified principal (`platform:actor`) who requested
  the action;
- policy: immutable `computer-requester-only-v1` Artifact;
- default TTL: `APPROVAL_TTL_SECONDS`;
- high and critical actions require approval even if the default gate is disabled.

The repository stores the decision, while `DurableComputerActionGateway` evaluates
whether the actor may decide that exact Action Intent. A different allowlisted user
cannot approve another user's task.

An existing Approval is evaluated against its stored policy Artifact rather than the
current process configuration, so a restart does not silently replace the governing
policy.

## Idempotency and recovery

- The Action Intent idempotency key is `computer:<platform>:<message_id>`.
- The action request contains the source Inbox message key and a frozen `ComputerTask`.
- Approval creation is idempotent per Action Intent.
- Startup and `/status` repair a pending `computer.execute` Intent if a crash occurred
  after Intent commit but before Approval creation.
- A provider execution requires a lease and fencing token.
- The Receipt ID is deterministic for the Intent and fencing token.
- Repeating an approval after a successful execution reads the stored observation and
  Receipt; it does not call the Provider again.
- Concurrent approval messages can resolve the same Approval, but only one caller can
  claim and execute the Action Intent.
- A known provider response records `SUCCEEDED` or `FAILED`.
- A provider exception after invocation records `UNKNOWN`, moving the Intent to
  `RECONCILE_REQUIRED`; it is not blindly retried.

Action detail responses under `/runtime/actions/{intent_id}` now include the linked
Receipt history.

## Removed compatibility state

The following product-path types were removed:

- `ApprovalStore`;
- `PendingApproval`;
- compatibility `ApprovalStatus`.

`app.state.approvals` no longer exists. `app.state.computer_actions` references the
durable gateway; Runtime approval projections remain available under
`/runtime/approvals`.

## Acceptance evidence

Automated tests cover:

- approval request and Provider execution across an application restart;
- recovery of an Action Intent committed immediately before Approval creation;
- durable pending-approval count after restart;
- requester-only authorization;
- durable denial and cancelled Action Intent;
- repeated approval after success without re-execution;
- two concurrent approval messages with one Provider call and one Receipt;
- approval-disabled medium-risk execution still producing Intent and Receipt;
- high-risk actions retaining an approval gate;
- Provider exception producing `UNKNOWN` and `RECONCILE_REQUIRED` without raw error
  text in Receipt evidence;
- computer and approval commands failing closed without a durable Inbox record;
- Feishu webhook restart from `/computer` to `/approve`;
- Runtime action detail exposing Receipt projections.

## Explicitly deferred

V2-002 converges the approval and external-action truth boundary; it does not finish
the whole task lifecycle:

- the compatibility gateway temporarily claims and executes the Action Intent inline;
  V2-004 moves execution into the bounded Worker Host;
- a crash after Inbox commit but before compatibility handling can still leave a queued
  Run without its Action Intent; the Worker path closes this recovery gap;
- scheduler Run, Thread, Delivery, Attention, and Outbox are not atomically finalized
  until V2-006;
- outbound Feishu replies are not transactional before the Outbox slice;
- interactive Feishu approval cards arrive with the later decision UI;
- capability/domain-specific approver roles beyond `requester_only` remain future
  policy work;
- `RECONCILE_REQUIRED` has durable state but no Provider-specific reconciler in this
  slice.

The next ordered slice is V2-003: durable Thread updates, route confirmation,
correction, cancellation, and method-change semantics.
