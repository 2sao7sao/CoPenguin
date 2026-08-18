# Contributing to CoPenguin

Thank you for helping make CoPenguin safer and more useful. This is an early
Alpha runtime: small, reviewable changes with explicit failure behavior are
more valuable than broad autonomous features.

## Before opening a change

1. Check existing issues and pull requests.
2. For a behavior or schema change, open an issue describing the user problem,
   trust boundary, failure cases, and acceptance criteria.
3. Branch from the latest `origin/main`. Do not rebuild the repository from an
   old prompt or use another feature branch as an implicit product baseline.
4. Use a stacked pull request only when it is labeled `stacked-pr`, names its
   parent PR, and will be rebased onto `main` before release.

```bash
git fetch origin
git switch main
git pull --ff-only
git switch -c feature/short-description
```

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
pre-commit install
```

Run the same gates used by CI:

```bash
ruff format --check src tests scripts
ruff check src tests scripts
pytest -q
mypy --ignore-missing-imports src/super_agent_runtime src/copenguin
python -m build
python -m pip_audit
```

## Runtime contribution rules

- Durable state changes are immutable events; projections must be replayable.
- Reducers are pure and cannot call models, tools, providers, memory, or KB.
- External effects use `Intent -> fenced claim -> Provider -> Receipt`.
- Retrieved data is not permission to act or permission to remember.
- Model output may propose memory, knowledge, hook, skill, or permission
  changes; it cannot promote them directly.
- New provider paths must fail closed, declare their side effects, and include
  idempotency or reconciliation behavior.
- Keep V2 milestone boundaries explicit. A partial path must not claim a
  Delivery was verified, accepted, published, or learned.

## Pull requests

Keep commits focused and use imperative subjects, for example
`Add deterministic delivery verifier`. A pull request must explain:

- what changed and why;
- user and developer impact;
- trust or privacy implications;
- migration and rollback behavior;
- tests and manual checks performed;
- any capability that remains simulated or unverified.

Do not commit credentials, local databases, private source material, generated
artifacts, or user memory. By contributing, you agree that your contribution is
licensed under the repository's Apache-2.0 license.

## Reporting security issues

Do not open a public issue for a vulnerability. Follow [SECURITY.md](SECURITY.md).
