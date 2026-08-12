# Security Model

This project treats Feishu messages as untrusted remote input.

## Defaults

- Feishu senders are denied unless allowlisted.
- Group messages require a mention by default.
- All computer tasks require approval by default.
- The default computer provider is `dry-run`.
- The shell provider is disabled unless `LOCAL_SHELL_ENABLED=1`.
- The shell provider only runs executables listed in `LOCAL_SHELL_ALLOWLIST`.
- The local Inbox write endpoint accepts loopback clients only; remote channel
  input must pass its channel authentication boundary first.

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

Only then does the provider run.

## Durable Side-Effect Boundary

CoPenguin stores an immutable action request artifact and a durable Intent
before calling an external provider. The worker claim carries an expiring lease
and fencing token. A provider response becomes a Receipt linked to the Intent.

If a worker disappears after the provider call but before writing the Receipt,
the Intent becomes `RECONCILE_REQUIRED`. Recovery must query the provider using
the original idempotency key; it cannot blindly repeat the action.

## Production Gaps

Before real computer control:

- persist approvals and audit logs;
- add Feishu interactive-card approvals with callback validation;
- add per-task timeout and cancellation;
- add screenshot/DOM redaction policy;
- separate read-only, reversible, and irreversible tools;
- require stronger confirmation for `critical` tasks;
- sandbox non-owner/group sessions;
- rotate Feishu app secrets if leaked.

## Recommended Policy

Use separate channels:

- DM with owner allowlist for privileged tasks;
- group chats for read-only/status tasks only;
- a dedicated Feishu bot app for this assistant, not a shared enterprise app.
