# Wyrd errors and recovery

## Stream and exit contract

With `--json`, successful commands emit one compact JSON value plus a newline on
stdout and leave stderr empty.

Ordinary failures emit one envelope on stderr and leave stdout empty:

```json
{"error":{"code":"ticket_not_found","details":{"ticket_id":99},"message":"Ticket 99 was not found."}}
```

- Exit `0`: success.
- Exit `1`: expected application, storage, confirmation, or diagnostic failure.
- Exit `2`: command usage or argument parsing failure.

`wyrd doctor --json` is the exception. A healthy report exits 0. An unhealthy report
exits 1 but remains a result on stdout:

```json
{"healthy":false,"problems":[...]}
```

Branch on `error.code`; messages are for people. Preserve `details` when reporting a
failure.

## Common recovery decisions

| Code | Meaning and response |
|---|---|
| `usage_error` | Correct command syntax or identity format; do not retry unchanged. |
| `project_not_found` | Move to the intended project. Initialize only when authorized. |
| `project_already_exists`, `nested_project` | Resolve project scope; do not create another nested project. |
| `invalid_project`, `corrupt_data`, `unsupported_schema` | Run `wyrd doctor --json`; do not edit `.wyrd/` directly. |
| `ticket_not_found`, `task_not_found` | Refresh lists and verify the selected identity. |
| `validation_error`, `invalid_label` | Correct the supplied value using error details. |
| `revision_conflict` | Re-read the resource, reconcile changes, and issue a new intentional edit. |
| `resource_not_active` | The resource or its parent is terminal; choose active work instead. |
| `blocked_by_open_dependency` | Inspect active blockers and finish or dismiss them first. |
| `ticket_has_open_tasks` | Complete or dismiss the ticket's open tasks first. |
| `invalid_dependency_scope` | Use ticket-to-ticket or sibling-task dependencies only. |
| `dependency_cycle` | Choose a dependency ordering that does not create a cycle. |
| `confirmation_required` | If the transition is authorized, rerun with `--yes`; otherwise stop. |
| `conflict` | Refresh state and reassess; the requested state may already be incompatible. |
| `lock_timeout` | Retry only if waiting is appropriate, preferably with a bounded timeout. |
| `transaction_error`, `storage_error` | Stop and report the storage failure; avoid speculative writes. |
| `internal_error` | Stop and report the failure without assuming whether a write occurred. |

## Diagnostic reports

Doctor is read-only and does not repair. Each problem contains `path`, `code`,
`message`, and `details`. Multiple independent structural and domain problems can be
reported together. Treat the report as evidence for a deliberate repair outside the
normal skill workflow, not as permission to modify managed files automatically.
