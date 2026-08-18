# Security Policy

## Supported versions

CoPenguin is an early Alpha. Security fixes are applied to the latest code on
`main`; no older release line is currently supported.

| Version | Supported |
| --- | --- |
| `main` / latest Alpha | Yes |
| Older snapshots | No |

## Report a vulnerability

Please use GitHub's private vulnerability reporting for this repository:

1. Open the repository **Security** tab.
2. Choose **Report a vulnerability**.
3. Include the affected commit, reproduction steps, impact, and any suggested
   mitigation.

Do not include credentials, private user memory, or sensitive source material in
the report. Please do not open a public issue before a fix or mitigation is
available. If private reporting is unavailable, contact the repository owner
through the private contact listed on their GitHub profile and mention only that
you have a CoPenguin security report.

## Response targets

- acknowledgement within 7 days;
- initial severity assessment within 14 days;
- coordinated disclosure after a fix is available, when practical.

These are targets, not a service-level guarantee for an unfunded Alpha project.

## Security posture

The detailed runtime threat model, safe defaults, secret handling, action
boundary, and known limitations live in [docs/SECURITY.md](docs/SECURITY.md).
CoPenguin defaults to local data, an empty Feishu allowlist, approval-gated
actions, and a non-mutating `dry-run` provider. Never expose the local control
API beyond loopback without adding authentication and scoped artifact access.
