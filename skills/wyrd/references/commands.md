# Wyrd command reference

This reference describes Wyrd 0.1.x. Run `wyrd <command> --help` when the installed
version differs.

## Shared conventions

- Ticket IDs are canonical positive decimals such as `3`; `03` is invalid.
- Task IDs are `<ticket-id>.<task-number>` strings such as `3.2`.
- Status values are `open`, `completed`, `dismissed`, and, for filters, `all`.
- Labels match `[a-z0-9][a-z0-9:_]{0,19}` and may be repeated where documented.
- `--json` emits compact deterministic JSON. `--no-color` affects human output.
- Project commands other than `init` accept `--lock-timeout SECONDS`, defaulting to
  `10.0`; zero is valid.
- `--body TEXT` and `--body-file PATH` are mutually exclusive. A body file must be
  UTF-8, and `--body-file -` reads stdin.

## Project commands

### `wyrd init`

```text
wyrd init [--name NAME] [--json] [--no-color]
```

Initializes the current directory. It has no `--lock-timeout` option.

### `wyrd status`

```text
wyrd status [--json] [--no-color] [--lock-timeout SECONDS]
```

Returns project, ticket, task, and label counts.

### `wyrd doctor`

```text
wyrd doctor [--json] [--no-color] [--lock-timeout SECONDS]
```

Performs a read-only structural and domain scan. An unhealthy report exits 1 while
placing the report on stdout.

## Ticket commands

### `wyrd ticket create`

```text
wyrd ticket create --title TITLE [--body TEXT | --body-file PATH]
  [--label LABEL]... [common options]
```

Creates an open ticket.

### `wyrd ticket list`

```text
wyrd ticket list [--status STATUS] [--label LABEL]... [--text TEXT]
  [--summary] [common options]
```

Defaults to `--status open`. Repeated labels use AND semantics. Text searches the
title and body after Unicode normalization and case folding. The optional `--summary`
flag makes each JSON entry a compact projection with `type`, `id`, `revision`,
`title`, `status`, `labels`, `is_blocked`, `active_blocked_by`, `active_blocking`, and
`tasks_summary`. It omits the body, timestamps, historical dependency directions,
activity flag, and complete task IDs. Without `--summary`, JSON entries remain complete
ticket objects.

### `wyrd ticket view`

```text
wyrd ticket view TICKET_ID [common options]
```

Returns one complete ticket, including its body, task summary, and dependency fields.

### `wyrd ticket edit`

```text
wyrd ticket edit TICKET_ID [--title TITLE] [--body TEXT | --body-file PATH]
  [--add-label LABEL]... [--remove-label LABEL]...
  [--expected-revision REVISION] [common options]
```

Requires at least one explicit field or label change. The same label cannot be added
and removed in one invocation. Only active tickets are editable.

### `wyrd ticket complete`

```text
wyrd ticket complete TICKET_ID [--yes] [common options]
```

Requires confirmation for a real transition. Noninteractive agents must use `--yes`.

### `wyrd ticket dismiss`

```text
wyrd ticket dismiss TICKET_ID [--yes] [common options]
```

Dismisses without changing child task statuses.

### `wyrd ticket dependency add`

```text
wyrd ticket dependency add BLOCKED_ID --blocked-by BLOCKER_ID [common options]
```

### `wyrd ticket dependency remove`

```text
wyrd ticket dependency remove BLOCKED_ID --blocked-by BLOCKER_ID [common options]
```

### `wyrd ticket dependency list`

```text
wyrd ticket dependency list TICKET_ID [common options]
```

JSON output is the complete ticket object; dependency directions are in `blocked_by`
and `blocking`, with effective relations in their `active_` counterparts.

## Task commands

### `wyrd task create`

```text
wyrd task create --ticket TICKET_ID --title TITLE
  [--body TEXT | --body-file PATH] [--label LABEL]... [common options]
```

Creates an open task under an active ticket.

### `wyrd task list`

```text
wyrd task list --ticket TICKET_ID [--status STATUS] [--summary]
  [common options]
```

Defaults to `--status all`. The result can include open but inactive tasks whose parent
ticket is terminal. The optional `--summary` flag makes each JSON entry a compact
projection with `type`, `id`, `ticket_id`, `number`, `revision`, `title`, `status`,
`labels`, `active`, `is_blocked`, `active_blocked_by`, and `active_blocking`. It omits
the body, timestamps, and historical dependency directions. Without `--summary`, JSON
entries remain complete task objects.

### `wyrd task view`

```text
wyrd task view TASK_ID [common options]
```

### `wyrd task edit`

```text
wyrd task edit TASK_ID [--title TITLE] [--body TEXT | --body-file PATH]
  [--add-label LABEL]... [--remove-label LABEL]...
  [--expected-revision REVISION] [common options]
```

Requires an explicit change and an active task.

### `wyrd task complete`

```text
wyrd task complete TASK_ID [--yes] [common options]
```

### `wyrd task dismiss`

```text
wyrd task dismiss TASK_ID [--yes] [common options]
```

### `wyrd task dependency add`

```text
wyrd task dependency add BLOCKED_ID --blocked-by BLOCKER_ID [common options]
```

Both IDs must be sibling tasks.

### `wyrd task dependency remove`

```text
wyrd task dependency remove BLOCKED_ID --blocked-by BLOCKER_ID [common options]
```

### `wyrd task dependency list`

```text
wyrd task dependency list TASK_ID [common options]
```

## Label commands

### `wyrd label list`

```text
wyrd label list [common options]
```

Returns distinct labels with ticket, task, and total usage counts.

## Common options

`[common options]` means:

```text
[--json] [--no-color] [--lock-timeout SECONDS]
```
