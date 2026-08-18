# Feishu Setup

CoPenguin supports webhook callbacks and an optional Feishu long-connection
WebSocket transport. Long connection is the preferred laptop-only path because
it does not require a public inbound URL. Both transports enter the same durable
Inbox, actor allowlist, Approval, Intent, and Receipt boundaries.

## App Setup

1. Create a self-built app in Feishu Open Platform.
2. Enable bot capability.
3. Add event subscription for `im.message.receive_v1` and card callback
   `card.action.trigger`.
4. For webhook mode, configure callback URL:

```text
https://<your-public-host>/feishu/events
```

5. Set the same verification token in Feishu and `FEISHU_VERIFICATION_TOKEN`.
6. Publish the app version after changing permissions or event subscriptions.

Webhook callbacks fail closed when `FEISHU_VERIFICATION_TOKEN` is absent. The
long-connection SDK authenticates with App credentials and therefore does not
trust a caller-supplied webhook token.

## Long connection (recommended for local use)

Install the optional official SDK and export credentials:

```bash
python -m pip install -e ".[feishu]"
export FEISHU_APP_ID="cli_xxx"
export FEISHU_APP_SECRET="..."
export FEISHU_VERIFICATION_TOKEN="..."
export FEISHU_ALLOWED_OPEN_IDS="ou_xxx"
copenguin feishu-long-connection
```

The SDK authenticates the WebSocket with the app credentials. CoPenguin still
applies the owner allowlist and durable message idempotency. No public port or
tunnel is required.

## Webhook development

Use a tunnel such as Tailscale Funnel, Cloudflare Tunnel, or ngrok:

```bash
copenguin serve
```

Then point Feishu to:

```text
https://<tunnel-domain>/feishu/events
```

## Required Environment

```bash
export FEISHU_APP_ID="cli_xxx"
export FEISHU_APP_SECRET="..."
export FEISHU_BOT_OPEN_ID="ou_bot_xxx"
export FEISHU_VERIFICATION_TOKEN="..."
export FEISHU_ALLOWED_OPEN_IDS="ou_xxx"
```

If you do not configure `FEISHU_ALLOWED_OPEN_IDS` or `FEISHU_ALLOWED_UNION_IDS`, the service will ignore remote messages unless `TRUST_ALL_FEISHU_USERS_FOR_DEV=1`.

For group chats, set `FEISHU_BOT_OPEN_ID` so mention gating can verify that the message mentioned this bot, not just any user.

Accepted messages receive the durable identity `feishu:<message_id>` before the
assistant handles them. A webhook retry, including one after restart, returns
the stored route instead of invoking the assistant again.

## Interactive approval cards

When `/computer` creates a pending Approval, configured Feishu clients receive
an interactive card with **Approve** and **Deny** buttons. Each button binds an
exact approval ID and schema. Callback actors still pass the allowlist and the
requester-only policy; re-delivered callbacks use the same durable Inbox key and
cannot execute the action twice. Text `/approve` and `/deny` commands remain as
an accessible fallback.

The implementation and mocked callback tests are included in the repository.
A real-app smoke test still requires your Feishu credentials, app permissions,
published app version, and callback subscription.

## Commands In Feishu

```text
/status
/remember 回答先给结论
/kb 我应该怎样设计这个 agent 的权限模型？
/computer 打开浏览器检查今天的日程
/approve <id>
/deny <id>
```

## Webhook Encryption

Encrypted webhook payloads are rejected in this MVP. Use one of these paths:

- disable callback encryption for local MVP testing;
- use the implemented Feishu long-connection mode;
- add Feishu AES decrypt support before exposing the webhook broadly.
