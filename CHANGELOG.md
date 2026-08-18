# Changelog

All notable changes to CoPenguin are documented here. The project follows
[Semantic Versioning](https://semver.org/) and the structure of
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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

`0.1.0` will be the first tagged Alpha after the convergence pull request is
merged and the release checklist in [docs/RELEASING.md](docs/RELEASING.md)
passes. Until that tag exists, the package version is a release candidate, not
evidence of a published release.

[Unreleased]: https://github.com/2sao7sao/CoPenguin/commits/main
