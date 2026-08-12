# CoPenguin Roadmap

## Current decision

The accepted V2 theme is **Trusted Closure**: make one real message become one
isolated, recoverable, verifiable, and explicitly accepted Delivery before
expanding autonomy.

The accepted Alpha Golden Path is **Source to Inspectable Artifact**. This is a
scope decision awaiting interview and Pilot evidence, not a validation result.

Engineering proceeds in the accepted `V2-001 -> V2-007` order. V2-001 has met
its branch-level acceptance criteria; V2-002 is next. Each slice must pass its
own acceptance criteria before the next slice begins.

- [V2 product and engineering direction](V2_PRODUCT_ENGINEERING_DIRECTION.md)
- [V2 Runtime Contract](V2_RUNTIME_CONTRACT.md)

The implementation order is determined by the product loop, not by the number
of available channels, tools, or model Providers.

## Gate 0: Product mechanism validation

- [x] Define the initial target-segment hypothesis and alternatives
- [x] Define ethical continued-use and anti-addiction boundaries
- [x] Prepare the 12-15 participant problem-interview guide
- [x] Prepare the four-week pilot protocol and decision gates
- [x] Specify a consent-filtered Product Evidence event plane
- [ ] Complete 12-15 qualified problem interviews
- [x] Select Source to Inspectable Artifact as the Alpha Golden Path
- [ ] Confirm or falsify that workflow with interview and Pilot evidence
- [ ] Reduce and privacy-review the Pilot event catalog
- [ ] Run the four-week Pilot with 12 participants
- [ ] Decide proceed, narrow, repeat, or stop

Runtime work may proceed where it enables the Golden Path, safety, recovery, or
measurement. Product hypotheses remain hypotheses until this gate supplies
behavioral evidence.

## V2-A: Converge the real product path

- [x] Route Feishu, local UI, and CLI through one durable Ingress boundary
- [x] Add end-to-end inbound idempotency before route or execution
- [ ] Add outbound transactional Outbox
- [ ] Replace the product-path in-memory approval queue with durable Approvals
- [ ] Persist Thread updates, route corrections, confirmation, cancellation,
      and method-change Branch events
- [ ] Keep ordinary conversation durable without creating a TaskThread

Exit: every real message uses the same state and governance boundary.

## V2-B: Close the execution and Delivery loop

- [ ] Implement a bounded Worker Host and Executor Protocol
- [ ] Add Step lifecycle, budgets, cancellation, checkpoint, and tool/model trace
- [ ] Add Verifier Registry and versioned VerifierResult Artifacts
- [ ] Atomically finalize scheduler, Run, Thread, Delivery, Attention, and Outbox
- [ ] Persist `accept`, `revise`, `reject`, `defer`, and `take over` decisions
- [ ] Create a new immutable Run/Delivery version after a revision request

Exit: one Source-to-Artifact task survives restart and reaches an explicit user
decision with a complete causal trace.

## V2-C: Expose the local control plane

- [ ] Build a minimal local Control Room: Inbox, TaskThreads, Attention,
      Task Detail, Artifacts, Memory & Permissions
- [ ] Show and correct Route Decisions
- [ ] Show Delivery summary, evidence, decisions, diff, and next action
- [ ] Add local session authentication and scoped Artifact access
- [ ] Require explicit opt-in before binding beyond loopback

Exit: users can understand and control parallel work without reading logs or
SQLite tables.

## V2-D: Add governed learning

- [ ] Bind MemorySelectionManifest, KB, Skill, Hook, Permission, Verifier, and
      Provider snapshots to each Run
- [ ] Expose Memory candidate review, correction, rejection, expiry, and forget
- [ ] Implement a versioned Hook Registry with timeouts and failure policy
- [ ] Implement an event-derived Observation Monitor
- [ ] Open ReviewCases and remediation proposals with evidence and cooldown
- [ ] Implement only the minimal Product Evidence events required by the Pilot

Exit: learning produces inspectable candidates and ReviewCases, never direct
promotion.

## V2-E: Harden recovery and ownership

- [ ] Run crash injection across Ingress, Step, Provider, Receipt, Delivery, and
      Outbox boundaries
- [ ] Add multi-process contention and scheduler chaos tests
- [ ] Add event upcasters and versioned database migrations
- [ ] Add backup, export, deletion, restore, and replay-integrity verification
- [ ] Add Artifact metadata, sensitivity, retention, and cleanup policy
- [ ] Split the repository implementation into stores behind one UnitOfWork
- [ ] Benchmark latency, recovery time, storage growth, and 10k dormant Threads

Exit: a restored local installation reproduces projection hashes and does not
duplicate side effects or lose Delivery decisions.

## After V2: evaluated evolution

Only after the Product Pilot and V2 Runtime gates pass:

- candidate snapshots;
- held-out independent evaluation;
- shadow execution;
- human-approved canary promotion;
- post-promotion monitoring;
- pointer rollback.

Automatic self-promotion and L3 bounded auto-run remain disabled by default.

## Explicitly deferred

- broad real computer-use Providers;
- high-risk unattended actions;
- many messaging channels at once;
- consumer companion mechanics;
- enterprise multi-tenancy;
- dedicated hardware;
- model-weight self-optimization.
