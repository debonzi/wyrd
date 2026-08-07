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

Run `wyrd --help` to explore all available commands.

## Agent skill

Wyrd includes a first-party [Agent Skills](https://agentskills.io) package in
[`skills/wyrd`](skills/wyrd). It teaches coding agents to triage work with bounded
context, use deterministic JSON, preserve revision safety, and follow Wyrd lifecycle
and dependency rules.

The skill requires a compatible `wyrd` executable on `PATH`; installing the skill does
not install the Python CLI. From a checkout, try it in Pi with:

```console
pi --skill ./skills/wyrd
```

For normal use, install a version pinned to the matching Wyrd release:

```console
pi install git:github.com/debonzi/wyrd@v0.1.0
```

Pi discovers the top-level `skills/` directory automatically. Other Agent
Skills-compatible harnesses can install or copy `skills/wyrd` into their user- or
project-level skills directory.

The optional read-only helper returns compact ticket or task context without bodies or
timestamps:

```console
python3 /path/to/skills/wyrd/scripts/context.py tickets --label performance
python3 /path/to/skills/wyrd/scripts/context.py tasks --ticket 1
```

See [`skills/wyrd/SKILL.md`](skills/wyrd/SKILL.md) for the complete workflow.

## Development

Create the locked development environment and run the complete suite with:

```console
uv sync --locked
uv run pytest
```

The GitHub Actions workflow runs the same suite on Python 3.12 and 3.14. Skill tests
validate its metadata, documented command inventory and options, compatibility version,
and compact helper behavior against the installed CLI.
