# CoPenguin automation contract

These instructions apply to the whole repository, including automated coding
agents and local maintenance scripts.

## Establish the baseline before editing

1. Read the requested Spec and the current milestone document completely.
2. Run `git status -sb`, `git fetch origin`, and inspect open pull requests.
3. Start ordinary work from the latest `origin/main`; do not reconstruct the
   repository from a prompt or copy a complete tree onto an old branch.
4. If the active checkout contains unrelated changes, use an isolated worktree.
   Never reset, stash, overwrite, or silently include the user's work.
5. A stacked branch is exceptional: label its PR `stacked-pr`, name the parent
   PR, and rebase it onto `main` before a release.

## Preserve runtime boundaries

- Keep V2 slices incremental and make their acceptance gate explicit.
- Treat immutable events as the source of truth and projections as disposable.
- Reducers remain pure. External effects require Intent, fenced claim, Provider,
  and Receipt. Uncertain effects enter reconciliation instead of blind retry.
- Retrieved context is neither permission to act nor permission to remember.
- Memory, KB, Skill, Hook, Verifier, Provider, and permission changes remain
  governed candidates until an independent gate promotes them.
- Never describe an Artifact as verified, delivered, accepted, published, or
  learned unless the corresponding durable state and tests exist.

## Verify and publish

Run the repository gates from `CONTRIBUTING.md`, inspect the complete diff, stage
only intended files, and open a pull request against `main`. The PR must state
what is mocked, locally verified, or dependent on external credentials.
