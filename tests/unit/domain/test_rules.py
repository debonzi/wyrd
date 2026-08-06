from __future__ import annotations

import pytest

from wyrd_cli.domain.errors import CorruptDataError
from wyrd_cli.domain.models import ResourceStatus
from wyrd_cli.domain.rules import (
    cyclic_nodes,
    invert_edges,
    summarize_tasks,
    task_effective_blockers,
    task_is_active,
    ticket_effective_blockers,
    validate_task_graph,
    validate_ticket_graph,
    would_create_cycle,
)
from tests.support.factories import task, ticket


def test_ticket_and_task_activity() -> None:
    open_ticket = ticket(1)
    dismissed_ticket = ticket(2, status=ResourceStatus.DISMISSED)
    open_task = task(1, 1)
    assert ticket_effective_blockers(open_ticket, {1: open_ticket}) == ()
    assert task_is_active(open_task, open_ticket)
    assert not task_is_active(task(2, 1), dismissed_ticket)
    assert not task_is_active(
        task(1, 2, status=ResourceStatus.COMPLETED), open_ticket
    )


def test_effective_blockers_require_both_endpoints_active() -> None:
    blocked = ticket(1, blocked_by=(2, 3))
    active = ticket(2)
    closed = ticket(3, status=ResourceStatus.COMPLETED)
    assert ticket_effective_blockers(blocked, {1: blocked, 2: active, 3: closed}) == (2,)

    parent = ticket(10)
    blocked_task = task(10, 3, blocked_by=(1, 2))
    siblings = {
        1: task(10, 1),
        2: task(10, 2, status=ResourceStatus.DISMISSED),
        3: blocked_task,
    }
    assert task_effective_blockers(blocked_task, parent, siblings) == (1,)
    assert task_effective_blockers(
        blocked_task,
        ticket(10, status=ResourceStatus.DISMISSED),
        siblings,
    ) == ()


def test_inverse_edges_and_cycle_detection_include_ineffective_edges() -> None:
    edges = {1: (2,), 2: (3,), 3: ()}
    assert invert_edges(edges) == {1: (), 2: (1,), 3: (2,)}
    assert would_create_cycle(edges, 3, 1)
    assert not would_create_cycle(edges, 1, 3)
    assert cyclic_nodes({1: (2,), 2: (1,), 3: ()}) == (1, 2)


@pytest.mark.parametrize(
    "tickets",
    [
        {1: ticket(1, blocked_by=(2,))},
        {1: ticket(1, blocked_by=(1,))},
        {1: ticket(1, blocked_by=(2,)), 2: ticket(2, blocked_by=(1,))},
    ],
)
def test_invalid_ticket_graph_is_corrupt(tickets: dict) -> None:
    with pytest.raises(CorruptDataError):
        validate_ticket_graph(tickets)


def test_invalid_task_graph_is_corrupt() -> None:
    parent = ticket(1)
    with pytest.raises(CorruptDataError):
        validate_task_graph(parent, {1: task(2, 1)})
    with pytest.raises(CorruptDataError):
        validate_task_graph(parent, {1: task(1, 1, blocked_by=(2,))})


def test_task_summary_distinguishes_inactive_open_tasks() -> None:
    parent = ticket(1, status=ResourceStatus.DISMISSED)
    values = [
        task(1, 1),
        task(1, 2, status=ResourceStatus.COMPLETED),
        task(1, 3, status=ResourceStatus.DISMISSED),
    ]
    summary = summarize_tasks(values, parent)
    assert summary.total == 3
    assert summary.open == 1
    assert summary.active_open == 0
    assert summary.inactive_open == 1
