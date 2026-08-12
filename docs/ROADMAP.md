# Roadmap

## Phase 0.5: Product Mechanism Validation

- [x] Define the initial target-segment hypothesis and alternatives
- [x] Define ethical continued-use and anti-addiction boundaries
- [x] Prepare the 12-15 participant problem-interview guide
- [x] Prepare the four-week pilot protocol and decision gates
- [x] Specify a consent-filtered Product Evidence event plane
- [ ] Complete 12-15 qualified problem interviews
- [ ] Select one primary workflow from demonstrated evidence
- [ ] Reduce and privacy-review the pilot event catalog
- [ ] Run the four-week pilot with 12 participants
- [ ] Decide proceed, narrow, repeat, or stop

Automatic self-evolution remains gated until repeated delegation, accepted
outcomes, memory correctness, and bounded trust expansion are observed. Runtime
engineering may continue where it is required for pilot safety or measurement.

## Phase 1: Local Safe MVP

- Webhook receive/send
- Owner allowlist
- Approval queue
- Dry-run computer provider
- EvolveMemory/EvolveKB optional adapters

Status: implemented.

## Phase 2: Feishu-Native Control Plane

- Long-connection WebSocket mode via `lark_oapi`
- Interactive-card streaming status
- Interactive-card approval buttons
- File/image/audio ingestion
- Feishu document/comment event ingestion

## Phase 1.5: Durable Multi-Task Runtime

- [x] Event Journal and deterministic Thread/Run replay
- [x] Sidebar-oriented Thread projection
- [x] Optimistic Thread single-writer control
- [x] Durable scheduler claim, heartbeat, retry, and fencing
- [x] Cross-Thread resource read/write leases
- [x] TaskSnapshot, AgentSnapshot, and ContextManifest
- [x] Artifact content-addressed storage
- [x] Intent/Receipt external side-effect boundary
- [x] Atomic Task submission and Thread Coordinator claim/start
- [x] Fenced checkpoint and crash recovery handoff
- [x] Inbox routing into new Thread, existing Thread, or non-task chat
- [x] Persistent Approval gate linked to Action Intent
- [ ] Bind Feishu/local ingress to the durable Inbox route
- [ ] Step/verifier/Delivery lifecycle and atomic terminal completion
- [ ] Hook Registry and self-loop observation service

## Phase 3: Real Computer Use

- Provider for OpenAI computer-use capable model
- Provider for local MCP computer-use server
- Provider for browser automation
- Provider for macOS automation
- Observation store with screenshots, tool calls, and audit trail

## Phase 4: Personal Assistant Behavior

- EvolveKB playbooks for recurring personal workflows
- EvolveMemory correction, review, and forget flows exposed in Feishu
- Daily/weekly scheduled routines
- Multi-session continuity across Feishu DM, group, and desktop

## Phase 5: Hardening

- Scheduler chaos and multi-process contention tests
- Capability-scoped tokens
- Per-sender and per-chat tool policy
- Encrypted webhook support or WebSocket-only deployment
- CI gates for knowledge and memory behavior
