# Wyrd lifecycle and dependency rules

Read this reference before changing statuses or dependencies.

## Status and activity

Resources have one of three statuses: `open`, `completed`, or `dismissed`. There is no
reopen command in Wyrd 0.1.x.

- An open ticket is active.
- A task is active only when both the task and its parent ticket are open.
- Editing and dependency changes operate on active resources.
- Requesting the same terminal status again is idempotent.
- Requesting the opposite terminal status conflicts.

Dismissing a ticket does not cascade writes to its tasks. Open tasks retain `open`
status but become inactive. Consequently, task list output can contain an open task
whose `active` field is false.

## Completion

A ticket can be completed only when:

1. it is open;
2. it has no open tasks; and
3. it has no effective open ticket blocker.

An active task can be completed only when it has no effective open sibling blocker.
Dismissal does not require blockers to be resolved, but the resource must still be
active.

For agent workflows, resolve or dismiss blockers first, finish child tasks, and then
complete the parent ticket. Pass `--yes` for authorized noninteractive transitions;
this suppresses prompting but never bypasses domain rules.

## Dependency direction

In:

```text
wyrd task dependency add 4.2 --blocked-by 4.1
```

`4.2` is blocked and `4.1` is the blocker. Wyrd stores the relation on the blocked
resource and derives the reverse `blocking` direction.

Dependencies are allowed only:

- from one ticket to another ticket; or
- between tasks belonging to the same ticket.

Adding a dependency requires both resources to be active and rejects self-relations,
cross-scope task relations, and cycles. Adding an existing relation and removing an
absent relation are idempotent. Removing a relation requires the blocked resource to
remain active.

## Effective and historical relations

A stored relation can remain after its blocker becomes terminal. Use these fields:

- `blocked_by` and `blocking`: all stored direct relations;
- `active_blocked_by` and `active_blocking`: currently effective open relations;
- `is_blocked`: whether at least one effective blocker exists.

Use active fields when deciding what can run now. Use complete fields when inspecting
history or editing the graph.

## Revisions and concurrency

Every semantic write increments the changed resource's revision. Ticket and task edit
commands accept `--expected-revision`; obtain it from a fresh `view` and pass it to
avoid overwriting concurrent edits.

Dependency and terminal commands do not expose an expected-revision option. They still
execute transactionally. On a conflict, re-read the affected resource and reassess the
operation rather than retrying automatically.
