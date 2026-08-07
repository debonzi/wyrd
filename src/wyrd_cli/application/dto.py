"""Typed requests and presentation-neutral application projections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from wyrd_cli.domain.models import ResourceStatus, TaskIdentity


class _Unset:
    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


UNSET = _Unset()
Unset = _Unset


@dataclass(frozen=True)
class CreateResourceRequest:
    title: str
    body: str = ""
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class EditResourceRequest:
    """Edit intent where ``UNSET`` is distinct from an explicit empty string."""

    title: str | Unset = UNSET
    body: str | Unset = UNSET
    add_labels: tuple[str, ...] = ()
    remove_labels: tuple[str, ...] = ()
    expected_revision: int | None = None


@dataclass(frozen=True)
class TicketListFilter:
    status: ResourceStatus | Literal["all"] = ResourceStatus.OPEN
    labels: tuple[str, ...] = ()
    text: str | None = None


@dataclass(frozen=True)
class TaskListFilter:
    status: ResourceStatus | Literal["all"] = "all"


class OutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProjectDTO(OutputModel):
    schema_version: int
    name: str
    created_at: datetime
    root: str


class TasksSummaryDTO(OutputModel):
    total: int
    open: int
    completed: int
    dismissed: int
    active_open: int
    inactive_open: int


class TicketSummaryDTO(OutputModel):
    type: Literal["ticket"] = "ticket"
    id: int
    revision: int
    title: str
    status: ResourceStatus
    labels: tuple[str, ...]
    is_blocked: bool
    active_blocked_by: tuple[int, ...]
    active_blocking: tuple[int, ...]
    tasks_summary: TasksSummaryDTO


class TaskSummaryDTO(OutputModel):
    type: Literal["task"] = "task"
    id: str
    ticket_id: int
    number: int
    revision: int
    title: str
    status: ResourceStatus
    labels: tuple[str, ...]
    active: bool
    is_blocked: bool
    active_blocked_by: tuple[str, ...]
    active_blocking: tuple[str, ...]


class TicketDTO(OutputModel):
    type: Literal["ticket"] = "ticket"
    id: int
    revision: int
    title: str
    body: str
    status: ResourceStatus
    labels: tuple[str, ...]
    blocked_by: tuple[int, ...]
    blocking: tuple[int, ...]
    active_blocked_by: tuple[int, ...]
    active_blocking: tuple[int, ...]
    active: bool
    is_blocked: bool
    tasks: tuple[str, ...]
    tasks_summary: TasksSummaryDTO
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None


class TaskDTO(OutputModel):
    type: Literal["task"] = "task"
    id: str
    ticket_id: int
    number: int
    revision: int
    title: str
    body: str
    status: ResourceStatus
    labels: tuple[str, ...]
    blocked_by: tuple[str, ...]
    blocking: tuple[str, ...]
    active_blocked_by: tuple[str, ...]
    active_blocking: tuple[str, ...]
    active: bool
    is_blocked: bool
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None


class LabelUsageDTO(OutputModel):
    name: str
    ticket_count: int
    task_count: int
    total_count: int


class TicketStatusCountsDTO(OutputModel):
    total: int
    open: int
    completed: int
    dismissed: int
    blocked: int


class TaskStatusCountsDTO(OutputModel):
    total: int
    open: int
    completed: int
    dismissed: int
    active_open: int
    inactive_open: int
    blocked: int


class LabelsStatusDTO(OutputModel):
    distinct: int


class ProjectStatusDTO(OutputModel):
    project: ProjectDTO
    tickets: TicketStatusCountsDTO
    tasks: TaskStatusCountsDTO
    labels: LabelsStatusDTO


class DoctorProblemDTO(OutputModel):
    path: str
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class DoctorReportDTO(OutputModel):
    healthy: bool
    problems: tuple[DoctorProblemDTO, ...]


class DependencyRelationDTO(OutputModel):
    """One direct relationship enriched inside the read snapshot."""

    id: int | str
    status: ResourceStatus
    effective: bool


class DependencyViewDTO(OutputModel):
    """Human-facing dependency detail plus the normative complete resource."""

    resource: TicketDTO | TaskDTO
    blocked_by: tuple[DependencyRelationDTO, ...]
    blocking: tuple[DependencyRelationDTO, ...]


class TransitionPreflightDTO(OutputModel):
    id: int | str
    title: str
    status: ResourceStatus
    revision: int
    target_status: ResourceStatus
    open_tasks: int = 0


ResourceIdentity = int | TaskIdentity
