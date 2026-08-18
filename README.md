<p align="center">
  <img src="assets/copenguin-logo.png" alt="CoPenguin chibi penguin-suit mascot gliding forward" width="176" />
</p>

<h1 align="center">CoPenguin</h1>

<p align="center">
  <strong>One inbox. Many isolated task threads. Work you can inspect and trust.</strong>
</p>

<p align="center">
  <a href="./README.zh.md">简体中文</a> ·
  <a href="./docs/V2_PRODUCT_ENGINEERING_DIRECTION.md">V2 Direction</a> ·
  <a href="./docs/PRODUCT_DISCOVERY.md">Product Discovery</a> ·
  <a href="./docs/RUNTIME_ARCHITECTURE.md">Runtime Architecture</a> ·
  <a href="./docs/PILOT_PROTOCOL.md">Pilot Protocol</a>
</p>

<p align="center">
  <a href="https://github.com/2sao7sao/CoPenguin/actions/workflows/ci.yml"><img src="https://github.com/2sao7sao/CoPenguin/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI" /></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-ff5aa5" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/version-v0.1.0-b8eee4" alt="Version 0.1.0" />
  <img src="https://img.shields.io/badge/posture-local--first-ff5aa5" alt="Local-first" />
</p>

<img src="assets/readme-banner.svg" alt="CoPenguin banner with its chibi penguin-suit mascot" width="100%" />

> [!IMPORTANT]
> **Status: early Alpha.** V2-001 through V2-003 have branch-level test coverage;
> V2-004 and the end-to-end Source-to-Artifact closure are still under active
> development. CoPenguin does not autonomously promote memory, skills, hooks,
> or permissions.

A useful personal agent should accept work through one natural chat surface without
turning every request into one tangled transcript. CoPenguin routes each message as
conversation, a new task, an update to existing work, or an ambiguity that needs the
owner's decision. Durable work receives its own `TaskThread`, causal history, snapshots,
checkpoints, approvals, artifacts, and receipts—so several life and work tasks can move
forward without silently contaminating one another.

[EvolveMemory](https://github.com/2sao7sao/EvolveMemory) supplies governed
personalization. [EvolveKB](https://github.com/2sao7sao/EvolveKB) supplies executable,
verifiable knowledge. CoPenguin remains the orchestration and policy boundary.

<img src="docs/assets/copenguin-runtime-terminal.svg" alt="CoPenguin test-backed runtime contract" width="100%" />

## The 30-Second Product Loop

```text
one inbox
  -> conservative route: chat | new task | task update | ambiguous
  -> durable TaskThread + versioned snapshots
  -> fenced worker + recoverable checkpoint
  -> action intent + approval when required
  -> provider execution + receipt + reconciliation
  -> inspectable delivery and governed learning candidates
```

The Alpha Golden Path is **Source → Inspectable Artifact**: turn an explicitly selected
source into a reviewable result, then let the owner accept, revise, reject, or publish
it. The final learning step is deliberately a candidate boundary: runtime evidence may
propose a memory, skill, hook, or permission change, but it cannot promote itself.

## What Ships in v0.1.0

| Surface | Current capability |
| --- | --- |
| Durable history | Append-only events, deterministic replay, projection hashes, causal IDs |
| Task isolation | Project → `TaskThread` → Run, with a single-writer rule per Thread |
| Parallel work | Durable queue, worker leases, fencing tokens, shared/exclusive resource locks |
| Recovery | Immutable Artifact CAS and Task/Agent/Context snapshots bound to each Run |
| Inbox decisions | Chat, new task, durable task update, control, or owner-confirmed ambiguity |
| External effects | Intent → approval → provider → Receipt, with crash reconciliation |
| Operator views | Read-only projections for Threads, inbox routes, actions, and approvals |
| Entry points | Local CLI plus Feishu webhook MVP with owner allowlist |
| Optional intelligence | Adapters for EvolveMemory and EvolveKB |

## 5-Minute Local Path

```bash
git clone https://github.com/2sao7sao/CoPenguin.git
cd CoPenguin
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
pytest -q
```

Send one message through the current local assistant boundary:

```bash
export COMPUTER_PROVIDER=dry-run
copenguin local "/computer open calendar and summarize tomorrow"
```

Local messages now enter the durable Inbox first. Supply a stable ID only when
you deliberately want to retry the same channel message:

```bash
copenguin local "/task turn these sources into a reviewable brief" \
  --project work --message-id demo-source-1
```

Run the Feishu webhook service:

```bash
export FEISHU_VERIFICATION_TOKEN="your-token"
export FEISHU_ALLOWED_OPEN_IDS="ou_xxx"
export TRUST_ALL_FEISHU_USERS_FOR_DEV=0
export COMPUTER_PROVIDER=dry-run
copenguin serve
```

```bash
curl http://127.0.0.1:8787/healthz
```

`dry-run` performs no desktop mutation. Optional integrations can be installed
with `python -m pip install -e ".[evolve]"`.

## Why This Is Not Just Chat or a Task Manager

| Common approach | Missing control | CoPenguin boundary |
| --- | --- | --- |
| One chat for everything | Task identity, concurrency, recoverability | Messages are routed; durable work gets a `TaskThread` |
| A task list with status fields | Execution lineage and external-effect safety | Events, snapshots, checkpoints, intents, and receipts |
| Vector memory on every turn | Scope, provenance, correction, expiry | Governed EvolveMemory candidates and use gates |
| Retrieved documents | Executable procedures and validation | EvolveKB Playbooks, Skills, gates, and proposals |
| Autonomous self-editing | Independent evaluation and rollback | Candidate → evaluation → shadow → promotion → monitor → rollback |

## Architecture

```mermaid
flowchart LR
  U["Unified Inbox"] --> R["Conservative Router"]
  R --> C["Conversation"]
  R --> T["TaskThread"]
  R --> Q["Confirmation"]
  T --> S["Snapshots + Artifact CAS"]
  T --> W["Durable Scheduler"]
  W --> I["Action Intent"]
  I --> A["Approval Gate"]
  A --> P["Provider"]
  P --> X["Receipt + Reconciliation"]
  X --> D["Inspectable Delivery"]
  D --> M["Memory Candidate"]
  D --> K["Knowledge / Skill Proposal"]
  M --> G["Independent Governance"]
  K --> G
```

The event journal supports multiple linked histories instead of one overloaded
transcript: conversation, execution, decisions, artifacts, and governance.
See the [runtime architecture](docs/RUNTIME_ARCHITECTURE.md) and
[state/event protocol](docs/STATE_EVENT_SPEC.md).

## Product Validation Gate

Technical correctness is not product demand. The initial hypothesis is that
AI-native individual workers juggling several projects will repeatedly delegate
real tasks, accept inspectable results, and voluntarily expand one bounded
permission.

The pilot north star is **accepted closed-loop tasks per participant per active
week**. Time in app, message count, emotional dependence, and raw autonomy level
are not success metrics. Product Evidence is a separate, consent-filtered
observation plane; it cannot mutate runtime state or promote memory, knowledge,
skills, hooks, or permissions.

- [Target segment and discovery thesis](docs/PRODUCT_DISCOVERY.md)
- [Problem interview guide](docs/INTERVIEW_GUIDE.md)
- [Four-week pilot protocol](docs/PILOT_PROTOCOL.md)
- [Product Evidence protocol](docs/PRODUCT_EVIDENCE_SPEC.md)

## V2 Direction: Trusted Closure

V2 adopts **Trusted Closure** and does not start with broader autonomy. It first
converges the legacy message path and the durable Runtime into one testable
product loop:

```text
message -> route decision -> TaskThread -> Run/Steps -> verified Delivery
        -> accept/revise/reject -> governed learning candidate
```

Read the [product and engineering review](docs/V2_PRODUCT_ENGINEERING_DIRECTION.md)
and the implementation-facing [V2 Runtime Contract](docs/V2_RUNTIME_CONTRACT.md).
The proposal includes the current problem inventory, target product surface,
Hook and self-loop boundaries, ordered PR slices, and Definition of Done.
**Source to Inspectable Artifact** is the confirmed Alpha Golden Path; this is
a product-scope decision, not yet evidence that the workflow has passed the Pilot.
The first concrete recipe is specified in the
[Feishu Memory and Knowledge System v0.1](docs/FEISHU_KNOWLEDGE_SYSTEM_SPEC_V0.1.md):
an explicitly selected Feishu source becomes a verified Project Decision Record,
then an accepted Delivery can be published to a Wiki draft through durable approval.
The first three convergence slices have passed branch-level acceptance:
[V2-001 Unified Ingress](docs/V2_001_UNIFIED_INGRESS.md) provides restart-safe message
identity; [V2-002 Durable Product Approvals](docs/V2_002_DURABLE_PRODUCT_APPROVALS.md)
binds computer actions to durable Intent, Approval, claim, Artifact, and Receipt
records; [V2-003 Durable Thread Updates](docs/V2_003_DURABLE_THREAD_UPDATES.md) makes
supplements, goal changes, method Branches, cancellation, and ambiguous route decisions
durable. V2-004 is the next ordered slice.

## Stable vs Prototype

### Implemented and test-backed

- deterministic Thread/Run replay and optimistic revision checks;
- SQLite event journal plus disposable read projections;
- durable scheduling, lease fencing, resource conflicts, and checkpoint recovery;
- Feishu/local unified ingress, restart-safe inbound dedupe, normalized message
  Artifacts, and conservative persistent route decisions;
- durable Thread updates with immutable replacement snapshots/Runs, method-change
  Branch lineage, cancellation propagation, and owner-only route resolution;
- durable Action Intents, Receipts, approvals, expiry, and reconciliation;
- `/computer`, `/approve`, and `/deny` use the durable action boundary; requester-only
  policy snapshots and decision-evidence Artifacts survive restart;
- Feishu parsing, owner allowlist, text approval commands, `dry-run`, and opt-in allowlisted `local-shell`.

### Deliberately incomplete

- first-seen messages still pass from durable Ingress to the compatibility assistant;
  its computer gateway temporarily executes claimed Actions inline until V2-004;
- outbound response delivery is not transactional until the Outbox slice;
- Step/verifier/Delivery events and atomic terminal completion are next runtime slices;
- interactive Feishu cards, long-connection mode, and a real computer-use provider are not shipped;
- Product Evidence is specified but not an operational validation result;
- versioned hooks, self-loop monitoring, shadow evaluation, and autonomous promotion are planned, not enabled.

## Current Commands

- `/status`
- `/remember <text>`
- `/kb <question>`
- `/computer <task>`
- `/approve <id>`
- `/deny <id>`
- `/thread <thread-id> <supplement, goal change, method change, or cancellation>`
- `/route <message-key> thread <thread-id> [supplement|goal|method|cancel]`
- `/route <message-key> new|dismiss`

## Repository Map

```text
src/super_agent_runtime/      durable events, scheduler, snapshots, inbox, actions
src/feishu_computer_agent/    current Feishu and local assistant MVP boundary
src/copenguin/                public package and CLI entry point
tests/                        runtime and channel contract tests
docs/                         architecture, governance, security, and pilot specs
assets/                       reusable CoPenguin logo and README banner
```

Local data uses `.copenguin/` by default. Existing `.agent-data/` installations
are detected for compatibility; explicit `COPENGUIN_DATA_DIR` always wins.

## Security Defaults

- no configured owner allowlist means Feishu messages are ignored;
- computer tasks require approval by default;
- `COMPUTER_PROVIDER=dry-run` performs no real action;
- `local-shell` is opt-in and only runs allowlisted executables;
- encrypted Feishu webhooks are rejected in this MVP instead of being silently mishandled.

See [Security](docs/SECURITY.md) and [Feishu setup](docs/FEISHU_SETUP.md).

## Brand Assets

The primary mascot is an original chibi character in a penguin kigurumi: round
penguin hood, visible human face, wing sleeves, white belly, and yellow beak and
feet. Its forward lean, planted leading foot, lifted trailing foot, and offset
wings make the movement readable without scenery or speed lines. The mascot
follows the user-supplied visual reference without retaining its
background, watermark, character identity, or distinctive accessories. The README
banner keeps CoPenguin related to the
[EvolveKB](https://github.com/2sao7sao/EvolveKB) brand family.

- [Primary chibi penguin-suit mascot](assets/copenguin-logo.png)
- [Scalable vector mark](assets/copenguin-logo.svg)
- [README banner](assets/readme-banner.svg)
- [Asset provenance and refresh checklist](docs/assets/README.md)

Keep the standalone PNG and SVG marks when refreshing the banner.
