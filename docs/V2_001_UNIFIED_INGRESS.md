# V2-001 — Unified Ingress and Inbound Dedupe

Status: implemented and test-backed on the V2-001 branch

## Outcome

Feishu, local CLI, and the loopback local API now normalize messages through one
`IngressAdapter -> InboxCoordinator` boundary before channel-specific downstream
handling. The durable identity is `platform:message_id`.

For a confirmed new-task route, one SQLite transaction writes:

1. the Inbox dedupe record;
2. `conversation.message_received`;
3. `inbox.route_proposed` and, when policy permits, `inbox.route_confirmed`;
4. the first TaskThread and Run events;
5. the scheduler queue item.

The normalized envelope and text are immutable Artifact CAS objects. A retry
returns the stored route without rerunning the router. Reusing the same message
key with a different actor, chat, or text is an idempotency conflict.

## Channel behavior

- Feishu authentication and owner checks happen before durable ingress.
- Feishu webhook retries after restart return the stored route and do not invoke
  the legacy assistant a second time.
- `copenguin local` creates a unique message ID by default; `--message-id` makes
  a deliberate retry reproducible.
- `POST /runtime/inbox` supports a future local UI but rejects non-loopback
  clients until the V2 Control Room authentication slice is implemented.
- `COPENGUIN_DEFAULT_PROJECT_ID` selects the default Project when a channel does
  not provide one.

## Acceptance evidence

The automated suite covers:

- same-process retry;
- retry after repository and application restart;
- two concurrent writers accepting the same message;
- message-key collision with different payload;
- rollback between Inbox insertion and first Task submission;
- migration of legacy Inbox rows;
- Feishu restart retry;
- local loopback API retry and remote-client rejection.

## Explicitly deferred

V2-001 closes intake identity, not the whole product loop:

- the compatibility `PrivateAssistantAgent` still handles a first-seen message
  after durable routing; V2-002 removes its in-memory approval path;
- Thread-update application and route correction arrive in V2-003;
- queued Runs do not execute automatically until V2-004;
- a transactional outbound Outbox arrives with the later Delivery/finalization
  slices, so V2-001 chooses missed duplicate response over repeated execution;
- full Control Room authentication remains V2-010; the new write endpoint is
  loopback-only in the meantime.
