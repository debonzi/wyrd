# Wyrd

**Weave your work, not your workflow.**

Wyrd is a local-first ticket management CLI built for coding agents and equally comfortable for humans. It keeps project work close to the code, provides predictable commands, and offers deterministic JSON output for reliable automation.

## Why Wyrd?

- **Local and inspectable:** project data lives alongside your code as readable Markdown and YAML.
- **Agent-friendly:** stable commands and structured JSON make automation straightforward.
- **Human-friendly:** concise terminal views make everyday project tracking easy.
- **Portable:** tickets travel naturally with the project and its version history.
- **Focused:** tickets, tasks, labels, and dependencies without a hosted service or database.
- **Safe:** lifecycle and dependency rules keep project state consistent.

## Installation

Wyrd 0.1.0 supports Linux only and requires Python 3.12 or newer. Windows and
macOS are not currently supported. For released versions published on PyPI, install
Wyrd as an isolated CLI tool with `uv` (recommended):

```console
uv tool install wyrd-cli
```

[`pipx`](https://pipx.pypa.io/) is also supported:

```console
pipx install wyrd-cli
```

A plain `pip` installation should normally be performed inside a virtual environment:

```console
python -m pip install wyrd-cli
```

The distribution name is `wyrd-cli`; every installation method above provides the
`wyrd` command.

## Getting started

Initialize Wyrd in a project directory:

```console
wyrd init --name "My project"
```

Create and inspect a ticket:

```console
wyrd ticket create --title "Improve startup performance" --label performance
wyrd ticket list
wyrd ticket view 1
```

Break work into tasks:

```console
wyrd task create --ticket 1 --title "Measure the current startup time"
wyrd task create --ticket 1 --title "Optimize configuration loading"
wyrd task list --ticket 1
```

Express dependencies explicitly:

```console
wyrd task dependency add 1.2 --blocked-by 1.1
```

Complete work when it is ready:

```console
wyrd task complete 1.1 --yes
wyrd task complete 1.2 --yes
wyrd ticket complete 1 --yes
```

Use `--json` with project commands when integrating Wyrd with an agent or script:

```console
wyrd ticket list --json
```

For lower-token triage, add `--summary` to ticket and task list commands:

```console
wyrd ticket list --status open --summary --json
wyrd task list --ticket 3 --status open --summary --json
```

`--summary` changes the JSON projection, not merely its whitespace: it omits bodies,
timestamps, historical dependency fields, and complete ticket task IDs while retaining
stable identity, status, labels, active dependencies, blocking state, and task counts.
Without the flag, list JSON remains the complete, compatible resource projection.
Human list output is already concise and stays unchanged when `--summary` is present.

For a human-oriented tabular hierarchy of open tickets and all of their tasks, use:

```console
wyrd tree
```

The defaults are `--status open` and `--task-status all`. In human output, the `tasks`
column reports displayed tasks versus the ticket total, such as `2/5`. Ticket filters
reuse the existing list semantics:

```console
wyrd tree --label bug --text startup --task-status open
```

Tree JSON is always a compact nested projection of ticket and task summaries, without
bodies or timestamps. It is useful when an agent needs tasks from several tickets in
one consistent read:

```console
wyrd tree --status open --task-status open --json
```

Agents should still prefer `ticket list --summary --json` for initial triage and
`task list --ticket ... --summary --json` for one selected ticket, since an unfiltered
tree can use unnecessary context.

Run `wyrd --help` to explore all available commands.

## Agent skill

The Wyrd source repository includes a first-party
[Agent Skills](https://agentskills.io) package in
[`skills/wyrd`](https://github.com/debonzi/wyrd/tree/main/skills/wyrd). It teaches
coding agents to triage work with bounded context, use deterministic JSON, preserve
revision safety, and follow Wyrd lifecycle and dependency rules.

The skill requires a compatible `wyrd` executable on `PATH`. Installing the skill does
not install the Wyrd CLI. The skill is distributed separately from the matching Git
repository tag and is not bundled in the Python wheel or source distribution. From a
source checkout, try it in Pi with:

```console
pi --skill ./skills/wyrd
```

For normal use with Wyrd 0.1.0, install the repository package pinned to its matching
Git tag:

```console
pi install git:github.com/debonzi/wyrd@v0.1.0
```

Pi discovers the repository's top-level `skills/` directory automatically. Other
Agent Skills-compatible harnesses can obtain `skills/wyrd` from the matching Git tag
and install or copy it into their user- or project-level skills directory.

The skill uses the CLI's native read-only summary projections for bounded triage:

```console
wyrd ticket list --status open --label performance --summary --json
wyrd task list --ticket 1 --status open --summary --json
```

See
[`skills/wyrd/SKILL.md`](https://github.com/debonzi/wyrd/blob/main/skills/wyrd/SKILL.md)
for the complete workflow.

## Development

Create the locked development environment and run the complete suite with:

```console
uv sync --locked
uv run pytest
```

The GitHub Actions workflow runs the same suite on Python 3.12, 3.13, and 3.14. Skill
tests validate its metadata, documented command inventory and options, compatibility
version, and native summary workflow against the installed CLI.

Before publishing, run the canonical artifact build, validation, and isolated smoke
pipeline documented in `docs/releasing.md`. It builds into a caller-selected empty
directory and never uses the stale files in the repository's top-level `dist/`.
