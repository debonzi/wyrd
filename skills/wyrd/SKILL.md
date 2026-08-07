---
name: wyrd
description: Manage local Wyrd tickets, tasks, labels, lifecycle transitions, and dependencies efficiently. Use when selecting, inspecting, creating, editing, organizing, or completing work tracked by the wyrd CLI, or when diagnosing a Wyrd project.
license: MIT
compatibility: Requires the wyrd executable from wyrd-cli 0.1.x on PATH. The optional compact context helper requires Python 3.12 or newer.
metadata:
  version: "0.1.0"
---

# Wyrd

Use Wyrd through its CLI as the only supported interface to project tracking data.
Run commands from the project directory or one of its descendants; Wyrd discovers
the project root.

## Guardrails

- Never read or edit `.wyrd/` as an API. Use `wyrd` commands.
- Do not run `wyrd init` or mutate tracked work unless the user's request authorizes
  that state change.
- Prefer `--json` for deterministic agent-facing output and errors.
- Do not use interactive confirmation. For an authorized terminal transition, pass
  `--yes --json`.
- Do not retry a failed mutation blindly. Interpret its error code, refresh relevant
  state, and reconsider the operation.

## Efficient read workflow

1. Establish project scope and counts:

   ```bash
   wyrd status --json
   ```

   If no project is found, locate the intended project or ask before initializing one.

2. Triage with compact context instead of loading complete bodies. Resolve
   [scripts/context.py](scripts/context.py) relative to this `SKILL.md`, but keep the
   process working directory inside the target project:

   ```bash
   python3 <skill-root>/scripts/context.py tickets
   python3 <skill-root>/scripts/context.py tickets --label bug --text startup
   python3 <skill-root>/scripts/context.py tasks --ticket 3
   ```

   The helper is read-only, invokes the installed `wyrd` executable, and removes bodies
   and timestamps. If it is unavailable, use `wyrd ticket list --no-color` or
   `wyrd task list --ticket ID --no-color` for bounded triage. Direct JSON lists contain
   complete objects and can be large.

3. Load a complete object only after selecting it:

   ```bash
   wyrd ticket view 3 --json
   wyrd task view 3.2 --json
   ```

4. Read dependency detail only when ordering work or resolving a block:

   ```bash
   wyrd ticket dependency list 3 --json
   wyrd task dependency list 3.2 --json
   ```

Use list filters early. The compact helper defaults both lists to open resources.
Direct ticket lists default to open and support repeated `--label` filters plus
`--text`; direct task lists require `--ticket` and default to all statuses.

## Mutation workflow

Before mutating, inspect the selected resource and verify that the requested change is
still appropriate.

### Create

```bash
wyrd ticket create --title "Improve startup" --label performance --json
wyrd task create --ticket 3 --title "Measure baseline" --json
```

Use `--body-file PATH` for long Markdown and `--body-file -` for stdin instead of
putting a large body in the command line.

### Edit safely

Read the resource, retain its `revision`, and use optimistic concurrency:

```bash
wyrd ticket edit 3 --title "Improve cold startup" --expected-revision 2 --json
wyrd task edit 3.2 --add-label benchmark --expected-revision 1 --json
```

On `revision_conflict`, re-read the resource and do not overwrite concurrent changes
without reassessing the edit.

### Add dependencies

The resource before `--blocked-by` is the blocked resource:

```bash
wyrd ticket dependency add 3 --blocked-by 2 --json
wyrd task dependency add 3.2 --blocked-by 3.1 --json
```

Task dependencies are allowed only between siblings. Add dependencies only when the
work truly requires ordering; Wyrd rejects cycles and invalid scopes.

### Finish work

Complete active blockers first, then dependents. Complete or dismiss remaining open
tasks before completing their ticket:

```bash
wyrd task complete 3.1 --yes --json
wyrd task complete 3.2 --yes --json
wyrd ticket complete 3 --yes --json
```

Dismiss only when the work should not be completed. Dismissing a ticket does not
change its tasks; any open tasks become inactive.

## Errors and diagnostics

With `--json`, ordinary failures are one JSON envelope on stderr. Branch on
`error.code`, not message text. `wyrd doctor --json` is the exception: an unhealthy
report is JSON on stdout with exit status 1.

Use:

```bash
wyrd doctor --json
```

when normal reads report invalid or corrupt project data. Doctor diagnoses but does
not repair.

## References

- Read [references/commands.md](references/commands.md) for exact command syntax and
  defaults.
- Read [references/lifecycle.md](references/lifecycle.md) before changing statuses or
  dependency graphs.
- Read [references/errors.md](references/errors.md) when handling a nonzero exit or
  building automation around Wyrd.
