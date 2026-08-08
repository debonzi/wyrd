# Changelog

All notable changes to Wyrd will be documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

No changes yet.

## 0.1.0 - Pending

Initial alpha release for Linux, requiring Python 3.12 or newer.

### Added

- Local-first tickets and tasks stored as readable Markdown and YAML files.
- Labels, ticket dependencies, and dependency ordering between sibling tasks.
- Deterministic JSON output and compact summary projections for agent and script
  workflows.
- Lifecycle validation, optimistic revision checks, project locking, and transactional
  filesystem updates for safe concurrent use.
- A first-party Agent Skill distributed from the matching Git repository tag rather
  than in the Python wheel or source distribution.
