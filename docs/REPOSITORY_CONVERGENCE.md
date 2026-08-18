# Repository Convergence Record

Status: implementation in the convergence pull request

## What was observed

On 2026-08-18, the default `main` branch contained only the repository
initialization commit, so a normal clone did not contain the package, README, or
CI workflow. The implementation work existed in a chain of pull requests.

The branch graph was inspected with `git log --graph` and ancestry checks. The
important correction is that the seven implementation branches were not seven
unrelated full copies: they formed one incremental stack rooted at `main`:

```text
main
  -> product-validation-foundation
  -> v2-product-runtime-direction
  -> v2-001-unified-ingress
  -> feishu-knowledge-system-spec
  -> v2-002-durable-product-approvals
  -> v2-003-durable-thread-updates
  -> public-brand-readme
```

The structural failure was still serious: the stack was never integrated back
into the default branch. Therefore the public repository surface remained empty
even while branch-level CI passed.

## Chosen repair

This convergence branch starts from the tip of the reviewed stack, adds the
bounded V2-004 Worker Host, and targets `main` directly. One pull request can
therefore populate the default branch without replaying seven overlapping manual
merges. Earlier stacked pull requests remain useful review history but should be
closed as superseded after the convergence pull request merges.

## Prevention controls

- [AGENTS.md](../AGENTS.md) tells automated workers to fetch and branch from the
  latest `origin/main` instead of reconstructing a repository from context.
- [CONTRIBUTING.md](../CONTRIBUTING.md) makes `main` the ordinary integration
  target and documents explicit stacked-PR exceptions.
- CI rejects a pull request whose base is not the default branch unless it has
  the `stacked-pr` label.
- The pull request template requires authors to confirm baseline, validation,
  data, and trust-boundary impact.
- Branch protection should require the CI checks listed below after this pull
  request lands.

## Post-merge repository operations

The following operations cannot be completed by a code commit and should happen
only after the convergence pull request is green and merged:

1. close pull requests #1 through #7 as superseded, retaining their history;
2. enable default-branch protection with required pull requests and required CI
   checks (`pull-request-contract`, both Python test jobs, `package`, and
   `dependency-audit`);
3. disable force pushes and branch deletion for `main`;
4. verify the GitHub Community Standards page detects the new files;
5. run the release checklist and create `v0.1.0`.

Applying protection before `main` contains the workflow would create an unclear
or impossible required-check state, so it is intentionally ordered after merge.
