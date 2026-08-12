# Feishu Setup

The current MVP supports Feishu webhook callbacks. For a laptop-only assistant, long-connection WebSocket mode should be the next implementation because it avoids public inbound networking.

## App Setup

1. Create a self-built app in Feishu Open Platform.
2. Enable bot capability.
3. Add event subscription for `im.message.receive_v1`.
4. Configure callback URL:

```text
https://<your-public-host>/feishu/events
```

5. Set the same verification token in Feishu and `FEISHU_VERIFICATION_TOKEN`.
6. Publish the app version after changing permissions or event subscriptions.

## Local Development

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
- use Feishu long-connection mode in the next implementation;
- add Feishu AES decrypt support before exposing the webhook broadly.
