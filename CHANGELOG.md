# Changelog

All notable changes to CoPenguin are documented here. The project follows
[Semantic Versioning](https://semver.org/) and the structure of
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- a responsive, loopback-only local Control Room for Project-grouped parallel
  Threads, Attention, Run/Step lineage, Artifact previews, and Delivery history;
- composed Control Room read models that derive from Runtime projections and
  digest-verified Artifact CAS content without creating UI-owned truth;
- natural-language local task creation and all five existing Delivery decisions,
  including immutable revision Run/Delivery lineage, from the owner surface;
- replayable, idempotent Delivery decisions for accept, revise, reject, defer,
  and take-over outcomes;
- immutable revision Runs with newly frozen Task and Context snapshots;
- loopback Delivery decision API and Feishu Delivery card callback contract;
- independent Delivery replay verification and revision rollback injection.

## [0.1.0] - 2026-08-18

### Added

- bounded Worker Host and deterministic Source-to-Artifact Alpha path;
- one-command local demo and containerized service path;
- project license, contribution, conduct, security, issue, and PR policies;
- dependency pinning, automated dependency updates, package build, and audit
  gates.

### Changed

- consolidated the previously stacked V2 work into one reviewable path back to
  `main`;
- updated English and Chinese documentation to distinguish implemented,
  simulated, and externally unverified capabilities.

## Release policy

`0.1.0` is the first CoPenguin Alpha. A package version is considered published
only when its matching Git tag and GitHub Release exist and the checklist in
[docs/RELEASING.md](docs/RELEASING.md) has passed.

[Unreleased]: https://github.com/2sao7sao/CoPenguin/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/2sao7sao/CoPenguin/releases/tag/v0.1.0
