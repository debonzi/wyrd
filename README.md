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
