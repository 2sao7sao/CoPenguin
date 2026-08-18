## What changed

<!-- Describe the user-visible and architectural change. -->

## Why

<!-- Link the problem or issue. Explain the root cause for fixes. -->

## Trust and data impact

- [ ] No new external side effect, permission, secret, memory write, or data flow
- [ ] New trust/data behavior is described below and fails closed

<!-- Include approval, privacy, idempotency, recovery, and rollback implications. -->

## Validation

- [ ] `ruff format --check src tests scripts`
- [ ] `ruff check src tests scripts`
- [ ] `pytest -q`
- [ ] relevant mypy checks
- [ ] package or container build when affected

<!-- Paste concise results and distinguish mocks from real provider verification. -->

## Branch and release hygiene

- [ ] Branch started from the latest `origin/main`
- [ ] PR targets `main`, or is labeled `stacked-pr` and names its parent PR
- [ ] Documentation and `CHANGELOG.md` are updated when user behavior changes
- [ ] No credentials, private sources, local databases, or user memories are committed
