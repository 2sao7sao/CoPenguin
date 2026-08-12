# CoPenguin

CoPenguin 是一个 local-first 的私人助理 Agent Runtime。它以持久化 TaskThread
管理工作与生活任务，通过受治理的记忆、可执行知识和安全动作边界逐步获得自治能力。
飞书是当前首个消息入口，而不是 Runtime 本身。

## Design Inputs

- [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent): messaging gateway、skills、memory、approval、multi-platform routing。
- [openclaw/openclaw](https://github.com/openclaw/openclaw): local-first gateway、Feishu channel、DM/group access policy、dynamic agent routing。
- [2sao7sao/EvolveKB](https://github.com/2sao7sao/EvolveKB): execution-first knowledge、Skills、Playbooks、validation gates。
- [2sao7sao/EvolveMemory](https://github.com/2sao7sao/EvolveMemory): governed adaptive memory、write policy、memory-use gate、prompt-safe context。

## Current MVP

已实现：

- Feishu webhook endpoint: `POST /feishu/events`
- Feishu URL verification challenge
- `im.message.receive_v1` text/post message parsing
- owner allowlist access control
- `/computer <task>` computer task entrypoint
- text approval flow: `/approve <id>` / `/deny <id>`
- `dry-run` computer provider
- opt-in `local-shell` provider with executable allowlist
- optional EvolveMemory adapter
- optional EvolveKB adapter
- unit tests for parser, access control, approvals, and computer provider
- event-sourced CoPenguin Runtime core with deterministic Thread/Run replay
- durable multi-Thread scheduler with lease fencing and resource conflict control
- immutable Artifact CAS plus Task/Agent/Context snapshots bound to each Run
- Intent/Receipt action boundary with crash reconciliation instead of blind retry
- atomic Task submission plus fenced worker checkpoint recovery
- conservative Inbox routing that keeps chat, new Tasks, Task updates, and
  ambiguous messages separate
- persistent Approval gate linked to Action Intent and Thread attention
- read-only Runtime projections for Threads, Inbox routes, Actions, and Approvals

Runtime design and current implementation status:

- [Runtime architecture](docs/RUNTIME_ARCHITECTURE.md)
- [State and event protocol](docs/STATE_EVENT_SPEC.md)

## Product Validation Status

Runtime capability is not evidence of product demand. CoPenguin is currently
testing the hypothesis that AI-native, multi-project individual workers will
repeatedly delegate real tasks, accept inspectable results, and voluntarily
expand one bounded permission.

Product discovery and the research gate:

- [Product discovery and target segment](docs/PRODUCT_DISCOVERY.md)
- [Problem interview guide](docs/INTERVIEW_GUIDE.md)
- [Four-week pilot protocol](docs/PILOT_PROTOCOL.md)
- [Product Evidence event protocol](docs/PRODUCT_EVIDENCE_SPEC.md)

The product north star proposed for the pilot is accepted closed-loop tasks per
participant per active week. Time in app, message count, emotional dependence,
and raw autonomy level are not success metrics. Product Evidence is a separate,
consent-filtered observation plane and cannot mutate Runtime state or promote
memory, knowledge, skills, or permissions.

下一步应优先做：

- complete 12-15 problem interviews and select one validated pilot workflow
- run the four-week product mechanism pilot before autonomous self-evolution
- Feishu long-connection WebSocket runner via `lark_oapi`
- Feishu interactive-card approvals
- real computer-use provider: OpenAI CUA, local MCP computer-use, browser automation, or macOS automation
- bind Feishu/computer tasks to the durable Runtime scheduler and Thread state machine
- complete Step/verifier/Delivery events and atomic terminal completion
- add the versioned Hook Registry and event-derived self-loop monitor

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Optional Evolve integrations:

```bash
python -m pip install -e ".[evolve]"
```

## Configure

Copy `.env.example` values into your shell or process manager.

CoPenguin stores local Runtime data under `.copenguin/` by default. Existing
`.agent-data/` installations are detected automatically; an explicit
`COPENGUIN_DATA_DIR` always takes precedence.

Minimum local dev:

```bash
export FEISHU_VERIFICATION_TOKEN="your-token"
export FEISHU_ALLOWED_OPEN_IDS="ou_xxx"
export TRUST_ALL_FEISHU_USERS_FOR_DEV=0
export COMPUTER_PROVIDER=dry-run
```

Run:

```bash
copenguin serve
```

Health check:

```bash
curl http://127.0.0.1:8787/healthz
```

Local test without Feishu:

```bash
copenguin local "/computer open calendar and summarize tomorrow"
```

## Feishu Commands

- `/status`
- `/remember <text>`
- `/kb <question>`
- `/computer <task>`
- `/approve <id>`
- `/deny <id>`

## Security Defaults

The default posture is intentionally restrictive:

- no configured owner allowlist means Feishu messages are ignored;
- all computer tasks require approval by default;
- `COMPUTER_PROVIDER=dry-run` performs no real desktop action;
- `local-shell` must be explicitly enabled and only runs allowlisted executables;
- encrypted Feishu webhooks are rejected in this MVP instead of being silently mishandled.

See [docs/SECURITY.md](docs/SECURITY.md).
