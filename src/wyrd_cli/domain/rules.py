"""Pure lifecycle, activity, graph, and aggregation rules."""

from __future__ import annotations

from collections import Counter
from collections.abc import Hashable, Iterable, Mapping
from dataclasses import dataclass
from typing import TypeVar

from .errors import CorruptDataError
from .models import ResourceStatus, Task, Ticket

Node = TypeVar("Node", bound=Hashable)


@dataclass(frozen=True)
class TaskCounts:
    total: int
    open: int
    completed: int
    dismissed: int
    active_open: int
    inactive_open: int


def ticket_is_active(ticket: Ticket) -> bool:
    return ticket.status is ResourceStatus.OPEN


def task_is_active(task: Task, parent: Ticket) -> bool:
    return task.status is ResourceStatus.OPEN and ticket_is_active(parent)


def ticket_effective_blockers(
    ticket: Ticket, tickets: Mapping[int, Ticket]
) -> tuple[int, ...]:
    if not ticket_is_active(ticket):
        return ()
    return tuple(
        blocker_id
        for blocker_id in ticket.blocked_by
        if blocker_id in tickets and ticket_is_active(tickets[blocker_id])
    )


def task_effective_blockers(
    task: Task, parent: Ticket, siblings: Mapping[int, Task]
) -> tuple[int, ...]:
    if not task_is_active(task, parent):
        return ()
    return tuple(
        number
        for number in task.blocked_by
        if number in siblings and task_is_active(siblings[number], parent)
    )


def invert_edges(edges: Mapping[Node, Iterable[Node]]) -> dict[Node, tuple[Node, ...]]:
    inverse: dict[Node, list[Node]] = {node: [] for node in edges}
    for blocked, blockers in edges.items():
        for blocker in blockers:
            inverse.setdefault(blocker, []).append(blocked)
    return {node: tuple(sorted(dependents)) for node, dependents in inverse.items()}


def path_exists(edges: Mapping[Node, Iterable[Node]], start: Node, goal: Node) -> bool:
    pending = [start]
    visited: set[Node] = set()
    while pending:
        node = pending.pop()
        if node == goal:
            return True
        if node in visited:
            continue
        visited.add(node)
        pending.extend(edges.get(node, ()))
    return False


def would_create_cycle(
    edges: Mapping[Node, Iterable[Node]], blocked: Node, blocker: Node
) -> bool:
    return blocked == blocker or path_exists(edges, blocker, blocked)


def cyclic_nodes(edges: Mapping[Node, Iterable[Node]]) -> tuple[Node, ...]:
    """Return all nodes belonging to a directed cycle, deterministically.

    The traversal is iterative so valid projects with thousands of resources do
    not depend on Python's recursion limit.
    """

    result = {
        node
        for node in edges
        if any(path_exists(edges, target, node) for target in edges.get(node, ()))
    }
    return tuple(sorted(result))


def validate_ticket_graph(tickets: Mapping[int, Ticket]) -> None:
    for ticket in tickets.values():
        for blocker_id in ticket.blocked_by:
            if blocker_id not in tickets:
                raise CorruptDataError(
                    f"Ticket {ticket.id} references missing ticket {blocker_id}.",
                    {"ticket_id": ticket.id, "blocker_id": blocker_id},
                )
            if blocker_id == ticket.id:
                raise CorruptDataError(
                    f"Ticket {ticket.id} depends on itself.", {"ticket_id": ticket.id}
                )
    edges = {ticket.id: ticket.blocked_by for ticket in tickets.values()}
    if cycles := cyclic_nodes(edges):
        raise CorruptDataError(
            "The ticket dependency graph contains a cycle.",
            {"ticket_ids": list(cycles)},
        )


def validate_task_graph(parent: Ticket, tasks: Mapping[int, Task]) -> None:
    for task in tasks.values():
        if task.ticket_id != parent.id:
            raise CorruptDataError(
                f"Task {task.public_id} has an invalid parent identity.",
                {"task_id": task.public_id, "ticket_id": parent.id},
            )
        for blocker_number in task.blocked_by:
            if blocker_number not in tasks:
                raise CorruptDataError(
                    f"Task {task.public_id} references missing sibling {parent.id}.{blocker_number}.",
                    {
                        "task_id": task.public_id,
                        "blocker_id": f"{parent.id}.{blocker_number}",
                    },
                )
            if blocker_number == task.number:
                raise CorruptDataError(
                    f"Task {task.public_id} depends on itself.",
                    {"task_id": task.public_id},
                )
    edges = {task.number: task.blocked_by for task in tasks.values()}
    if cycles := cyclic_nodes(edges):
        raise CorruptDataError(
            f"Ticket {parent.id}'s task dependency graph contains a cycle.",
            {"ticket_id": parent.id, "task_numbers": list(cycles)},
        )


def summarize_tasks(tasks: Iterable[Task], parent: Ticket) -> TaskCounts:
    values = tuple(tasks)
    statuses = Counter(task.status for task in values)
    active_open = sum(task_is_active(task, parent) for task in values)
    open_count = statuses[ResourceStatus.OPEN]
    return TaskCounts(
        total=len(values),
        open=open_count,
        completed=statuses[ResourceStatus.COMPLETED],
        dismissed=statuses[ResourceStatus.DISMISSED],
        active_open=active_open,
        inactive_open=open_count - active_open,
    )
