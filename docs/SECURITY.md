# Security Model

This project treats Feishu messages as untrusted remote input.

## Defaults

- Feishu senders are denied unless allowlisted.
- Webhook callbacks fail closed unless `FEISHU_VERIFICATION_TOKEN` is configured;
  the authenticated official-SDK long connection is the only token-bypass path.
- Group messages require a mention by default.
- All computer tasks require approval by default.
- The default computer provider is `dry-run`.
- The shell provider is disabled unless `LOCAL_SHELL_ENABLED=1`.
- The shell provider only runs executables listed in `LOCAL_SHELL_ALLOWLIST`.
- The macOS Shortcuts provider is disabled unless `MACOS_SHORTCUTS_ENABLED=1`
  and only runs an exact name in `MACOS_SHORTCUTS_ALLOWLIST`.
- The local Inbox write endpoint accepts loopback clients only; remote channel
  input must pass its channel authentication boundary first.
- A proposed Inbox route can be resolved only by the original channel actor;
  the local decision endpoint is loopback-only and cross-Project updates fail closed.

## Approval Flow

User:

```text
/computer open browser and download monthly invoices
```

Agent:

```text
Computer task queued for approval.
id: abc123
risk: medium
approve: /approve abc123
deny: /deny abc123
```

User:

```text
/approve abc123
```

Only then does the durable gateway acquire a fenced Action claim and call the
provider. The request, policy, decision evidence, observation, and Receipt remain
inspectable after restart.

## Durable Side-Effect Boundary

CoPenguin stores an immutable action request artifact and a durable Intent
before calling an external provider. The worker claim carries an expiring lease
and fencing token. A provider response becomes a Receipt linked to the Intent.

If a worker disappears after the provider call but before writing the Receipt,
the Intent becomes `RECONCILE_REQUIRED`. Recovery must query the provider using
the original idempotency key; it cannot blindly repeat the action.

## Route and Cancellation Boundary

An ambiguous continuation remains `PROPOSED` and cannot mutate a Thread or enqueue
a Run. A later route command records the original actor, the chosen target, and the
reason before applying the effect atomically. Concurrent conflicting decisions cannot
both win.

Thread cancellation invalidates queued or claimed scheduler work, fences the old
Worker, and revokes pending Approvals plus unexecuted Action Intents for superseded
Runs. It does not claim to reverse an external Provider effect: an Action that may
already have crossed the Provider boundary still follows its Intent, Receipt, and
reconciliation policy.

## Production Gaps

Before broad vision-driven computer control:

- complete a credential-backed smoke test for Feishu long connection and
  interactive-card callbacks;
- enforce wall-clock and cost budgets for every future model/tool Step kind;
- add screenshot/DOM redaction policy;
- separate read-only, reversible, and irreversible tools;
- require stronger confirmation for `critical` tasks;
- sandbox non-owner/group sessions;
- rotate Feishu app secrets if leaked.

The current text-command policy is requester-only. More complex approver roles must
remain capability-, Project-, risk-, and scope-specific; writing `resolved_by` is not
itself proof of authorization.

## Recommended Policy

Use separate channels:

- DM with owner allowlist for privileged tasks;
- group chats for read-only/status tasks only;
- a dedicated Feishu bot app for this assistant, not a shared enterprise app.
