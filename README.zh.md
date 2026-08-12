<img src="assets/readme-banner.svg" alt="CoPenguin Banner 与围巾企鹅 Logo" width="100%" />

<p align="center">
  <a href="./README.md">English</a> ·
  <a href="./docs/V2_PRODUCT_ENGINEERING_DIRECTION.md">V2 优化方向</a> ·
  <a href="./docs/PRODUCT_DISCOVERY.md">产品发现</a> ·
  <a href="./docs/RUNTIME_ARCHITECTURE.md">Runtime 架构</a> ·
  <a href="./docs/PILOT_PROTOCOL.md">试点协议</a>
</p>

<p align="center">
  <a href="https://github.com/2sao7sao/CoPenguin/actions/workflows/ci.yml"><img src="https://github.com/2sao7sao/CoPenguin/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI" /></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-ff5aa5" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/version-v0.1.0-b8eee4" alt="Version 0.1.0" />
  <img src="https://img.shields.io/badge/posture-local--first-ff5aa5" alt="Local-first" />
</p>

# CoPenguin

**一个 local-first 的私人助理 Runtime：把含混请求变成持久、可检查、受治理的工作。**

私人助理不应该只是一个无限增长的聊天记录。CoPenguin 把“对话”和“工作”分开：
需要持续执行的任务拥有独立 `TaskThread`，并发 Run 共享因果历史却不会互相污染；
现实动作经过审批并留下回执。EvolveMemory 负责受治理的个性化，EvolveKB 负责可执行知识，
两者都不会暗中接管编排权。

<img src="docs/assets/copenguin-runtime-terminal.svg" alt="CoPenguin 经测试支撑的 Runtime 契约" width="100%" />

## 30 秒理解产品路径

```text
统一聊天入口
  -> 保守路由：普通对话 | 新任务 | 任务更新 | 目标不明确
  -> 持久 TaskThread + 版本化快照
  -> 带 fencing 的 Worker + 可恢复 checkpoint
  -> Action Intent + 必要时审批
  -> 外部 Provider 执行 + Receipt + 事故对账
  -> 可检查交付 + 受治理的学习候选项
```

最后一步刻意停在“候选项”：运行证据可以提出记忆、技能、Hook 或权限变更，
但不能自行把提案升级为正式能力。

## v0.1.0 已实现

| 能力面 | 当前能力 |
| --- | --- |
| 持久历史 | Append-only 事件、确定性 replay、projection hash、因果 ID |
| 任务隔离 | 项目 → `TaskThread` → Run；同一 Thread 的主 Run 遵守 single-writer |
| 并发运行 | 持久队列、Worker lease、fencing token、共享/独占资源锁 |
| 恢复能力 | 不可变 Artifact CAS，以及绑定到每个 Run 的 Task/Agent/Context 快照 |
| 入口判断 | 区分对话、新任务、任务更新、控制命令与需要确认的歧义 |
| 外部动作 | Intent → 审批 → Provider → Receipt，并支持崩溃对账 |
| 操作视图 | Thread、路由、动作、审批的只读 projection API |
| 当前入口 | 本地 CLI，以及带 owner allowlist 的飞书 webhook MVP |
| 可选智能层 | EvolveMemory 与 EvolveKB adapter |

## 5 分钟本地体验

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
pytest -q
```

通过当前本地助理边界发送一条消息：

```bash
export COMPUTER_PROVIDER=dry-run
copenguin local "/computer open calendar and summarize tomorrow"
```

启动飞书 webhook 服务：

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

`dry-run` 不会修改桌面状态。需要 Evolve 集成时，可执行
`python -m pip install -e ".[evolve]"`。

## 为什么不只是 Chat 或任务管理器

| 常见方案 | 缺失的控制 | CoPenguin 的边界 |
| --- | --- | --- |
| 所有事情都放在一个聊天框 | 任务身份、并发隔离、恢复 | 消息先路由，持久工作进入 `TaskThread` |
| 只有状态字段的任务列表 | 执行谱系与外部动作安全 | 事件、快照、checkpoint、Intent、Receipt |
| 每轮都写向量记忆 | 作用域、来源、更正、过期 | EvolveMemory 候选项与使用门禁 |
| 只检索文档 | 可执行流程与验证 | EvolveKB Playbook、Skill、gate、proposal |
| Agent 自己修改自己 | 独立评估与回滚 | 候选 → 评估 → 影子运行 → 晋升 → 监测 → 回滚 |

## 架构

```mermaid
flowchart LR
  U["统一 Inbox"] --> R["保守路由"]
  R --> C["普通对话"]
  R --> T["TaskThread"]
  R --> Q["请求确认"]
  T --> S["快照 + Artifact CAS"]
  T --> W["持久调度器"]
  W --> I["Action Intent"]
  I --> A["审批门"]
  A --> P["外部 Provider"]
  P --> X["Receipt + 对账"]
  X --> D["可检查交付"]
  D --> M["记忆候选项"]
  D --> K["知识 / Skill 提案"]
  M --> G["独立治理"]
  K --> G
```

事件日志不是一份臃肿聊天记录，而是支持多条同源历史：对话、执行、决策、产物和治理。
详见 [Runtime 架构](docs/RUNTIME_ARCHITECTURE.md) 与
[State/Event 协议](docs/STATE_EVENT_SPEC.md)。

## 产品验证门

技术正确不等于产品需求成立。当前首先验证：同时处理多个项目的 AI-native 个人工作者，
是否会反复委托真实任务、接受可检查的结果，并主动扩大一项有边界的权限。

试点北极星是**每位参与者每个活跃周被接受的闭环任务数**。使用时长、消息量、情感依赖和
粗粒度自治等级都不是成功指标。Product Evidence 是单独的、经用户同意过滤的观察面，
无权修改 Runtime，也无权晋升记忆、知识、Skill、Hook 或权限。

- [目标人群与产品发现假设](docs/PRODUCT_DISCOVERY.md)
- [问题访谈指南](docs/INTERVIEW_GUIDE.md)
- [四周试点协议](docs/PILOT_PROTOCOL.md)
- [Product Evidence 协议](docs/PRODUCT_EVIDENCE_SPEC.md)

## V2 方向：Trusted Closure / 可信闭环

V2 已确认采用 **Trusted Closure / 可信闭环**，不以扩大自治为起点，而是先把旧消息入口与
耐久 Runtime 收敛成一条可验证的产品主链：

```text
消息 -> 路由决定 -> TaskThread -> Run/Steps -> 经验证的 Delivery
     -> 接受/修改/拒绝 -> 受治理的学习候选项
```

详见 [产品与工程审计及 V2 方向](docs/V2_PRODUCT_ENGINEERING_DIRECTION.md) 和面向实现的
[V2 Runtime Contract](docs/V2_RUNTIME_CONTRACT.md)。其中包括当前问题清单、产品控制面、
Hook/self-loop 边界、按顺序拆分的 PR 与 Definition of Done。
**Source to Inspectable Artifact** 已确认为 Alpha 主路径；这是产品范围决定，不代表该工作流
已经通过用户 Pilot 验证。

## 稳定能力与原型边界

### 已实现并有测试支撑

- Thread/Run 确定性 replay 与 optimistic revision check；
- SQLite event journal 和可丢弃重建的只读 projection；
- 持久调度、lease fencing、资源冲突与 checkpoint 恢复；
- 保守 Inbox 路由与持久路由记录；
- Action Intent、Receipt、审批、过期与对账；
- 飞书解析、owner allowlist、文字审批、`dry-run` 和显式开启的 allowlisted `local-shell`。

### 刻意尚未完成

- 旧 `/computer` 与飞书消息链路尚未端到端接入 `InboxCoordinator`；
- Step/verifier/Delivery 事件和原子化结束仍是下一段 Runtime 工作；
- 飞书交互卡片、长连接和真实 computer-use provider 尚未交付；
- Product Evidence 目前是协议，不是已经得出的市场验证结论；
- 版本化 Hook、self-loop 监测、影子评估和自治晋升仍在规划中，默认没有启用。

## 当前命令

- `/status`
- `/remember <text>`
- `/kb <question>`
- `/computer <task>`
- `/approve <id>`
- `/deny <id>`

## 仓库结构

```text
src/super_agent_runtime/      持久事件、调度、快照、Inbox、动作治理
src/feishu_computer_agent/    当前飞书与本地助理 MVP 边界
src/copenguin/                公共 Python package 与 CLI 入口
tests/                        Runtime 与消息渠道契约测试
docs/                         架构、治理、安全和产品试点协议
assets/                       可复用的 CoPenguin Logo 与 README Banner
```

本地数据默认存储在 `.copenguin/`。为了兼容已有安装，会自动发现 `.agent-data/`；
显式设置的 `COPENGUIN_DATA_DIR` 始终优先。

## 安全默认值

- 未配置 owner allowlist 时，飞书消息会被忽略；
- computer task 默认需要审批；
- `COMPUTER_PROVIDER=dry-run` 不执行真实动作；
- `local-shell` 必须显式开启，并且只能运行 allowlist 中的可执行程序；
- 当前 MVP 会拒绝加密飞书 webhook，不会静默误处理。

详见 [安全说明](docs/SECURITY.md) 和 [飞书配置](docs/FEISHU_SETUP.md)。

## 品牌素材

围巾企鹅是仓库内持久保存的正式素材，不依赖外部图片链接。企鹅造型、深色网格、粉/薄荷配色
与终端视觉，刻意和 [EvolveKB](https://github.com/2sao7sao/EvolveKB) 保持同一品牌家族。

- [独立企鹅 Logo](assets/copenguin-logo.svg)
- [README Banner](assets/readme-banner.svg)
- [素材来源与更新检查表](docs/assets/README.md)

以后即使重做 Banner，也不要删除独立 Logo。
