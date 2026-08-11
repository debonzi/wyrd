"""Typer command tree and presentation-only orchestration."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, BinaryIO, TextIO, TypeVar

import typer

from wyrd_cli import __version__
from wyrd_cli.application.dto import (
    TaskListFilter,
    TicketListFilter,
)
from wyrd_cli.application.services import WyrdApplication
from wyrd_cli.domain.errors import ApplicationError
from wyrd_cli.domain.models import ResourceStatus
from wyrd_cli.domain.values import parse_task_id, parse_ticket_id

from .boundary import (
    JsonAwareTyperGroup,
    PresentationError,
    fail_expected,
    fail_unexpected,
)
from .input import (
    create_request,
    edit_request,
    validate_lock_timeout,
    validate_positive_option,
    validate_status,
)
from .rendering import (
    emit_json,
    render_dependencies,
    render_doctor,
    render_labels,
    render_project,
    render_resource,
    render_status,
    render_task_list,
    render_ticket_list,
    render_tree,
    styling_enabled,
)

T = TypeVar("T")


def _stdout() -> TextIO:
    return typer.get_text_stream("stdout")


def _stdin() -> TextIO:
    return typer.get_text_stream("stdin")


def _binary_stdin() -> BinaryIO:
    return typer.get_binary_stream("stdin")


def _confirm(message: str) -> bool:
    return typer.confirm(message, default=False)


def _version_callback(value: bool) -> bool:
    if value:
        typer.echo(__version__)
        raise typer.Exit()
    return value


@dataclass(frozen=True)
class PresentationDependencies:
    """Per-invocation providers used by the app factory and presentation tests."""

    application_factory: Callable[[], WyrdApplication]
    cwd: Callable[[], str] = os.getcwd
    stdout: Callable[[], TextIO] = _stdout
    stdin: Callable[[], TextIO] = _stdin
    binary_stdin: Callable[[], BinaryIO] = _binary_stdin
    confirm: Callable[[str], bool] = _confirm
    is_tty: Callable[[TextIO], bool] = lambda stream: bool(stream.isatty())


@dataclass(frozen=True)
class InvocationSettings:
    json_output: bool
    no_color: bool
    lock_timeout: float | None = None


def create_app(dependencies: PresentationDependencies) -> typer.Typer:
    """Build the exact public command tree around an injected application factory."""

    app = typer.Typer(
        name="wyrd",
        cls=JsonAwareTyperGroup,
        help="Manage local Wyrd tickets and tasks.",
        no_args_is_help=True,
        add_completion=True,
        suggest_commands=False,
        pretty_exceptions_enable=False,
    )
    ticket_app = typer.Typer(help="Manage tickets.", no_args_is_help=True)
    ticket_dependency_app = typer.Typer(
        help="Manage ticket dependencies.", no_args_is_help=True
    )
    task_app = typer.Typer(help="Manage tasks.", no_args_is_help=True)
    task_dependency_app = typer.Typer(
        help="Manage sibling task dependencies.", no_args_is_help=True
    )
    label_app = typer.Typer(help="Inspect inline labels.", no_args_is_help=True)

    app.add_typer(ticket_app, name="ticket")
    ticket_app.add_typer(ticket_dependency_app, name="dependency")
    app.add_typer(task_app, name="task")
    task_app.add_typer(task_dependency_app, name="dependency")
    app.add_typer(label_app, name="label")

    @app.callback()
    def root(
        version: bool = typer.Option(
            False,
            "--version",
            help="Show the Wyrd version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ) -> None:
        del version

    @app.command("init")
    def initialize(
        name: str | None = typer.Option(None, "--name"),
        json_output: bool = typer.Option(False, "--json"),
        no_color: bool = typer.Option(False, "--no-color"),
    ) -> None:
        """Initialize a project in the current directory."""

        settings = InvocationSettings(json_output, no_color)
        _run(
            dependencies,
            settings,
            lambda: dependencies.application_factory().initialize(
                dependencies.cwd(), name
            ),
            render_project,
        )

    @app.command("status")
    def status(
        json_output: bool = typer.Option(False, "--json"),
        no_color: bool = typer.Option(False, "--no-color"),
        lock_timeout: float = typer.Option(
            10.0, "--lock-timeout", callback=validate_lock_timeout
        ),
    ) -> None:
        """Show project summary counts."""

        settings = InvocationSettings(json_output, no_color, lock_timeout)
        _run(
            dependencies,
            settings,
            lambda: _with_project(
                dependencies,
                lambda application: application.project_status(
                    lock_timeout=lock_timeout
                ),
            ),
            render_status,
        )

    @app.command("tree")
    def tree(
        status_value: str = typer.Option(
            "open", "--status", callback=validate_status
        ),
        task_status_value: str = typer.Option(
            "all", "--task-status", callback=validate_status
        ),
        labels: list[str] = typer.Option([], "--label"),
        text: str | None = typer.Option(None, "--text"),
        json_output: bool = typer.Option(False, "--json"),
        no_color: bool = typer.Option(False, "--no-color"),
        lock_timeout: float = typer.Option(
            10.0, "--lock-timeout", callback=validate_lock_timeout
        ),
    ) -> None:
        """Show tickets and their tasks as a hierarchy tree."""

        settings = InvocationSettings(json_output, no_color, lock_timeout)
        ticket_filters = TicketListFilter(
            status=status_value,
            labels=tuple(labels),
            text=text,
        )
        task_filters = TaskListFilter(status=task_status_value)
        _run(
            dependencies,
            settings,
            lambda: _with_project(
                dependencies,
                lambda application: application.list_tree(
                    ticket_filters,
                    task_filters,
                    lock_timeout=lock_timeout,
                ),
            ),
            render_tree,
        )

    @app.command("doctor")
    def doctor(
        json_output: bool = typer.Option(False, "--json"),
        no_color: bool = typer.Option(False, "--no-color"),
        lock_timeout: float = typer.Option(
            10.0, "--lock-timeout", callback=validate_lock_timeout
        ),
    ) -> None:
        """Diagnose project structure and domain state without repair."""

        settings = InvocationSettings(json_output, no_color, lock_timeout)
        _run(
            dependencies,
            settings,
            lambda: _with_doctor_project(
                dependencies,
                lambda application: application.doctor(lock_timeout=lock_timeout),
            ),
            render_doctor,
            doctor_result=True,
        )

    @ticket_app.command("create")
    def ticket_create(
        title: str = typer.Option(..., "--title"),
        body: str | None = typer.Option(None, "--body"),
        body_file: str | None = typer.Option(None, "--body-file"),
        labels: list[str] = typer.Option([], "--label"),
        json_output: bool = typer.Option(False, "--json"),
        no_color: bool = typer.Option(False, "--no-color"),
        lock_timeout: float = typer.Option(
            10.0, "--lock-timeout", callback=validate_lock_timeout
        ),
    ) -> None:
        """Create an open ticket."""

        settings = InvocationSettings(json_output, no_color, lock_timeout)

        def action():
            request = create_request(
                title=title,
                body=body,
                body_file=body_file,
                labels=labels,
                binary_stdin=dependencies.binary_stdin,
            )
            return _with_project(
                dependencies,
                lambda application: application.create_ticket(
                    request, lock_timeout=lock_timeout
                ),
            )

        _run(dependencies, settings, action, render_resource)

    @ticket_app.command("list")
    def ticket_list(
        status_value: str = typer.Option(
            "open", "--status", callback=validate_status
        ),
        labels: list[str] = typer.Option([], "--label"),
        text: str | None = typer.Option(None, "--text"),
        json_output: bool = typer.Option(False, "--json"),
        summary: bool = typer.Option(
            False,
            "--summary",
            help="Use the compact ticket projection for JSON output.",
        ),
        no_color: bool = typer.Option(False, "--no-color"),
        lock_timeout: float = typer.Option(
            10.0, "--lock-timeout", callback=validate_lock_timeout
        ),
    ) -> None:
        """List tickets in ascending ID order."""

        settings = InvocationSettings(json_output, no_color, lock_timeout)
        filters = TicketListFilter(
            status=status_value,
            labels=tuple(labels),
            text=text,
        )

        def action():
            def operation(application: WyrdApplication):
                method = (
                    application.list_ticket_summaries
                    if summary and json_output
                    else application.list_tickets
                )
                return method(filters, lock_timeout=lock_timeout)

            return _with_project(dependencies, operation)

        _run(dependencies, settings, action, render_ticket_list)

    @ticket_app.command("view")
    def ticket_view(
        ticket_id: str = typer.Argument(...),
        json_output: bool = typer.Option(False, "--json"),
        no_color: bool = typer.Option(False, "--no-color"),
        lock_timeout: float = typer.Option(
            10.0, "--lock-timeout", callback=validate_lock_timeout
        ),
    ) -> None:
        """View a complete ticket."""

        settings = InvocationSettings(json_output, no_color, lock_timeout)
        def action():
            parsed_id = parse_ticket_id(ticket_id)
            return _with_project(
                dependencies,
                lambda application: application.view_ticket(
                    parsed_id, lock_timeout=lock_timeout
                ),
            )

        _run(dependencies, settings, action, render_resource)

    @ticket_app.command("edit")
    def ticket_edit(
        ticket_id: str = typer.Argument(...),
        title: str | None = typer.Option(None, "--title"),
        body: str | None = typer.Option(None, "--body"),
        body_file: str | None = typer.Option(None, "--body-file"),
        add_labels: list[str] = typer.Option([], "--add-label"),
        remove_labels: list[str] = typer.Option([], "--remove-label"),
        expected_revision: int | None = typer.Option(
            None, "--expected-revision", callback=validate_positive_option
        ),
        json_output: bool = typer.Option(False, "--json"),
        no_color: bool = typer.Option(False, "--no-color"),
        lock_timeout: float = typer.Option(
            10.0, "--lock-timeout", callback=validate_lock_timeout
        ),
    ) -> None:
        """Edit explicit fields of an active ticket."""

        settings = InvocationSettings(json_output, no_color, lock_timeout)

        def action():
            parsed_id = parse_ticket_id(ticket_id)
            request = edit_request(
                title=title,
                body=body,
                body_file=body_file,
                add_labels=add_labels,
                remove_labels=remove_labels,
                expected_revision=expected_revision,
                binary_stdin=dependencies.binary_stdin,
            )
            return _with_project(
                dependencies,
                lambda application: application.edit_ticket(
                    parsed_id, request, lock_timeout=lock_timeout
                ),
            )

        _run(dependencies, settings, action, render_resource)

    _register_ticket_transitions(ticket_app, dependencies)
    _register_ticket_dependencies(ticket_dependency_app, dependencies)

    @task_app.command("create")
    def task_create(
        ticket_id: str = typer.Option(..., "--ticket"),
        title: str = typer.Option(..., "--title"),
        body: str | None = typer.Option(None, "--body"),
        body_file: str | None = typer.Option(None, "--body-file"),
        labels: list[str] = typer.Option([], "--label"),
        json_output: bool = typer.Option(False, "--json"),
        no_color: bool = typer.Option(False, "--no-color"),
        lock_timeout: float = typer.Option(
            10.0, "--lock-timeout", callback=validate_lock_timeout
        ),
    ) -> None:
        """Create an open task under an active ticket."""

        settings = InvocationSettings(json_output, no_color, lock_timeout)

        def action():
            parsed_ticket_id = parse_ticket_id(ticket_id)
            request = create_request(
                title=title,
                body=body,
                body_file=body_file,
                labels=labels,
                binary_stdin=dependencies.binary_stdin,
            )
            return _with_project(
                dependencies,
                lambda application: application.create_task(
                    parsed_ticket_id, request, lock_timeout=lock_timeout
                ),
            )

        _run(dependencies, settings, action, render_resource)

    @task_app.command("list")
    def task_list(
        ticket_id: str = typer.Option(..., "--ticket"),
        status_value: str = typer.Option(
            "all", "--status", callback=validate_status
        ),
        json_output: bool = typer.Option(False, "--json"),
        summary: bool = typer.Option(
            False,
            "--summary",
            help="Use the compact task projection for JSON output.",
        ),
        no_color: bool = typer.Option(False, "--no-color"),
        lock_timeout: float = typer.Option(
            10.0, "--lock-timeout", callback=validate_lock_timeout
        ),
    ) -> None:
        """List tasks belonging to one ticket."""

        settings = InvocationSettings(json_output, no_color, lock_timeout)

        def action():
            parsed_ticket_id = parse_ticket_id(ticket_id)
            filters = TaskListFilter(status=status_value)

            def operation(application: WyrdApplication):
                method = (
                    application.list_task_summaries
                    if summary and json_output
                    else application.list_tasks
                )
                return method(
                    parsed_ticket_id,
                    filters,
                    lock_timeout=lock_timeout,
                )

            return _with_project(dependencies, operation)

        _run(dependencies, settings, action, render_task_list)

    @task_app.command("view")
    def task_view(
        task_id: str = typer.Argument(...),
        json_output: bool = typer.Option(False, "--json"),
        no_color: bool = typer.Option(False, "--no-color"),
        lock_timeout: float = typer.Option(
            10.0, "--lock-timeout", callback=validate_lock_timeout
        ),
    ) -> None:
        """View a complete task."""

        settings = InvocationSettings(json_output, no_color, lock_timeout)
        def action():
            identity = parse_task_id(task_id)
            return _with_project(
                dependencies,
                lambda application: application.view_task(
                    identity, lock_timeout=lock_timeout
                ),
            )

        _run(dependencies, settings, action, render_resource)

    @task_app.command("edit")
    def task_edit(
        task_id: str = typer.Argument(...),
        title: str | None = typer.Option(None, "--title"),
        body: str | None = typer.Option(None, "--body"),
        body_file: str | None = typer.Option(None, "--body-file"),
        add_labels: list[str] = typer.Option([], "--add-label"),
        remove_labels: list[str] = typer.Option([], "--remove-label"),
        expected_revision: int | None = typer.Option(
            None, "--expected-revision", callback=validate_positive_option
        ),
        json_output: bool = typer.Option(False, "--json"),
        no_color: bool = typer.Option(False, "--no-color"),
        lock_timeout: float = typer.Option(
            10.0, "--lock-timeout", callback=validate_lock_timeout
        ),
    ) -> None:
        """Edit explicit fields of an active task."""

        settings = InvocationSettings(json_output, no_color, lock_timeout)

        def action():
            identity = parse_task_id(task_id)
            request = edit_request(
                title=title,
                body=body,
                body_file=body_file,
                add_labels=add_labels,
                remove_labels=remove_labels,
                expected_revision=expected_revision,
                binary_stdin=dependencies.binary_stdin,
            )
            return _with_project(
                dependencies,
                lambda application: application.edit_task(
                    identity, request, lock_timeout=lock_timeout
                ),
            )

        _run(dependencies, settings, action, render_resource)

    _register_task_transitions(task_app, dependencies)
    _register_task_dependencies(task_dependency_app, dependencies)

    @label_app.command("list")
    def label_list(
        json_output: bool = typer.Option(False, "--json"),
        no_color: bool = typer.Option(False, "--no-color"),
        lock_timeout: float = typer.Option(
            10.0, "--lock-timeout", callback=validate_lock_timeout
        ),
    ) -> None:
        """List distinct inline label usage."""

        settings = InvocationSettings(json_output, no_color, lock_timeout)
        _run(
            dependencies,
            settings,
            lambda: _with_project(
                dependencies,
                lambda application: application.list_labels(
                    lock_timeout=lock_timeout
                ),
            ),
            render_labels,
        )

    return app


def _register_ticket_transitions(
    ticket_app: typer.Typer, dependencies: PresentationDependencies
) -> None:
    @ticket_app.command("complete")
    def complete(
        ticket_id: str = typer.Argument(...),
        yes: bool = typer.Option(False, "--yes"),
        json_output: bool = typer.Option(False, "--json"),
        no_color: bool = typer.Option(False, "--no-color"),
        lock_timeout: float = typer.Option(
            10.0, "--lock-timeout", callback=validate_lock_timeout
        ),
    ) -> None:
        """Complete an eligible ticket."""

        _ticket_transition(
            dependencies,
            ticket_id,
            ResourceStatus.COMPLETED,
            yes=yes,
            settings=InvocationSettings(json_output, no_color, lock_timeout),
        )

    @ticket_app.command("dismiss")
    def dismiss(
        ticket_id: str = typer.Argument(...),
        yes: bool = typer.Option(False, "--yes"),
        json_output: bool = typer.Option(False, "--json"),
        no_color: bool = typer.Option(False, "--no-color"),
        lock_timeout: float = typer.Option(
            10.0, "--lock-timeout", callback=validate_lock_timeout
        ),
    ) -> None:
        """Dismiss an active ticket without cascading to tasks."""

        _ticket_transition(
            dependencies,
            ticket_id,
            ResourceStatus.DISMISSED,
            yes=yes,
            settings=InvocationSettings(json_output, no_color, lock_timeout),
        )


def _register_task_transitions(
    task_app: typer.Typer, dependencies: PresentationDependencies
) -> None:
    @task_app.command("complete")
    def complete(
        task_id: str = typer.Argument(...),
        yes: bool = typer.Option(False, "--yes"),
        json_output: bool = typer.Option(False, "--json"),
        no_color: bool = typer.Option(False, "--no-color"),
        lock_timeout: float = typer.Option(
            10.0, "--lock-timeout", callback=validate_lock_timeout
        ),
    ) -> None:
        """Complete an eligible active task."""

        _task_transition(
            dependencies,
            task_id,
            ResourceStatus.COMPLETED,
            yes=yes,
            settings=InvocationSettings(json_output, no_color, lock_timeout),
        )

    @task_app.command("dismiss")
    def dismiss(
        task_id: str = typer.Argument(...),
        yes: bool = typer.Option(False, "--yes"),
        json_output: bool = typer.Option(False, "--json"),
        no_color: bool = typer.Option(False, "--no-color"),
        lock_timeout: float = typer.Option(
            10.0, "--lock-timeout", callback=validate_lock_timeout
        ),
    ) -> None:
        """Dismiss an active task."""

        _task_transition(
            dependencies,
            task_id,
            ResourceStatus.DISMISSED,
            yes=yes,
            settings=InvocationSettings(json_output, no_color, lock_timeout),
        )


def _register_ticket_dependencies(
    dependency_app: typer.Typer, dependencies: PresentationDependencies
) -> None:
    @dependency_app.command("add")
    def add(
        blocked_id: str = typer.Argument(...),
        blocker_id: str = typer.Option(..., "--blocked-by"),
        json_output: bool = typer.Option(False, "--json"),
        no_color: bool = typer.Option(False, "--no-color"),
        lock_timeout: float = typer.Option(
            10.0, "--lock-timeout", callback=validate_lock_timeout
        ),
    ) -> None:
        """Add a ticket dependency."""

        settings = InvocationSettings(json_output, no_color, lock_timeout)
        def action():
            blocked = parse_ticket_id(blocked_id)
            blocker = parse_ticket_id(blocker_id)
            return _with_project(
                dependencies,
                lambda application: application.add_dependency(
                    blocked, blocker, lock_timeout=lock_timeout
                ),
            )

        _run(dependencies, settings, action, render_resource)

    @dependency_app.command("remove")
    def remove(
        blocked_id: str = typer.Argument(...),
        blocker_id: str = typer.Option(..., "--blocked-by"),
        json_output: bool = typer.Option(False, "--json"),
        no_color: bool = typer.Option(False, "--no-color"),
        lock_timeout: float = typer.Option(
            10.0, "--lock-timeout", callback=validate_lock_timeout
        ),
    ) -> None:
        """Remove a ticket dependency."""

        settings = InvocationSettings(json_output, no_color, lock_timeout)
        def action():
            blocked = parse_ticket_id(blocked_id)
            blocker = parse_ticket_id(blocker_id)
            return _with_project(
                dependencies,
                lambda application: application.remove_dependency(
                    blocked, blocker, lock_timeout=lock_timeout
                ),
            )

        _run(dependencies, settings, action, render_resource)

    @dependency_app.command("list")
    def list_relations(
        ticket_id: str = typer.Argument(...),
        json_output: bool = typer.Option(False, "--json"),
        no_color: bool = typer.Option(False, "--no-color"),
        lock_timeout: float = typer.Option(
            10.0, "--lock-timeout", callback=validate_lock_timeout
        ),
    ) -> None:
        """List direct ticket blockers and dependents."""

        settings = InvocationSettings(json_output, no_color, lock_timeout)
        def action():
            identity = parse_ticket_id(ticket_id)
            return _with_project(
                dependencies,
                lambda application: (
                    application.list_dependencies(
                        identity, lock_timeout=lock_timeout
                    )
                    if json_output
                    else application.dependency_details(
                        identity, lock_timeout=lock_timeout
                    )
                ),
            )

        _run(
            dependencies,
            settings,
            action,
            render_resource if json_output else render_dependencies,
        )


def _register_task_dependencies(
    dependency_app: typer.Typer, dependencies: PresentationDependencies
) -> None:
    @dependency_app.command("add")
    def add(
        blocked_id: str = typer.Argument(...),
        blocker_id: str = typer.Option(..., "--blocked-by"),
        json_output: bool = typer.Option(False, "--json"),
        no_color: bool = typer.Option(False, "--no-color"),
        lock_timeout: float = typer.Option(
            10.0, "--lock-timeout", callback=validate_lock_timeout
        ),
    ) -> None:
        """Add a sibling task dependency."""

        settings = InvocationSettings(json_output, no_color, lock_timeout)
        def action():
            blocked = parse_task_id(blocked_id)
            blocker = parse_task_id(blocker_id)
            return _with_project(
                dependencies,
                lambda application: application.add_dependency(
                    blocked, blocker, lock_timeout=lock_timeout
                ),
            )

        _run(dependencies, settings, action, render_resource)

    @dependency_app.command("remove")
    def remove(
        blocked_id: str = typer.Argument(...),
        blocker_id: str = typer.Option(..., "--blocked-by"),
        json_output: bool = typer.Option(False, "--json"),
        no_color: bool = typer.Option(False, "--no-color"),
        lock_timeout: float = typer.Option(
            10.0, "--lock-timeout", callback=validate_lock_timeout
        ),
    ) -> None:
        """Remove a sibling task dependency."""

        settings = InvocationSettings(json_output, no_color, lock_timeout)
        def action():
            blocked = parse_task_id(blocked_id)
            blocker = parse_task_id(blocker_id)
            return _with_project(
                dependencies,
                lambda application: application.remove_dependency(
                    blocked, blocker, lock_timeout=lock_timeout
                ),
            )

        _run(dependencies, settings, action, render_resource)

    @dependency_app.command("list")
    def list_relations(
        task_id: str = typer.Argument(...),
        json_output: bool = typer.Option(False, "--json"),
        no_color: bool = typer.Option(False, "--no-color"),
        lock_timeout: float = typer.Option(
            10.0, "--lock-timeout", callback=validate_lock_timeout
        ),
    ) -> None:
        """List direct sibling task blockers and dependents."""

        settings = InvocationSettings(json_output, no_color, lock_timeout)
        def action():
            identity = parse_task_id(task_id)
            return _with_project(
                dependencies,
                lambda application: (
                    application.list_dependencies(
                        identity, lock_timeout=lock_timeout
                    )
                    if json_output
                    else application.dependency_details(
                        identity, lock_timeout=lock_timeout
                    )
                ),
            )

        _run(
            dependencies,
            settings,
            action,
            render_resource if json_output else render_dependencies,
        )


def _ticket_transition(
    dependencies: PresentationDependencies,
    raw_id: str,
    target: ResourceStatus,
    *,
    yes: bool,
    settings: InvocationSettings,
) -> None:
    timeout = 10.0 if settings.lock_timeout is None else settings.lock_timeout

    def action():
        ticket_id = parse_ticket_id(raw_id)

        def operation(application: WyrdApplication):
            if yes:
                method = (
                    application.complete_ticket
                    if target is ResourceStatus.COMPLETED
                    else application.dismiss_ticket
                )
                return method(ticket_id, lock_timeout=timeout)
            preflight = application.transition_ticket_preflight(
                ticket_id, target, lock_timeout=timeout
            )
            method = (
                application.complete_ticket
                if target is ResourceStatus.COMPLETED
                else application.dismiss_ticket
            )
            if preflight.status is target:
                return method(
                    ticket_id,
                    expected_revision=preflight.revision,
                    lock_timeout=timeout,
                )
            _require_confirmation(dependencies, settings, preflight)
            return method(
                ticket_id,
                expected_revision=preflight.revision,
                lock_timeout=timeout,
            )

        return _with_project(dependencies, operation)

    _run(dependencies, settings, action, render_resource)


def _task_transition(
    dependencies: PresentationDependencies,
    raw_id: str,
    target: ResourceStatus,
    *,
    yes: bool,
    settings: InvocationSettings,
) -> None:
    timeout = 10.0 if settings.lock_timeout is None else settings.lock_timeout

    def action():
        identity = parse_task_id(raw_id)

        def operation(application: WyrdApplication):
            if yes:
                method = (
                    application.complete_task
                    if target is ResourceStatus.COMPLETED
                    else application.dismiss_task
                )
                return method(identity, lock_timeout=timeout)
            preflight = application.transition_task_preflight(
                identity, target, lock_timeout=timeout
            )
            method = (
                application.complete_task
                if target is ResourceStatus.COMPLETED
                else application.dismiss_task
            )
            if preflight.status is target:
                return method(
                    identity,
                    expected_revision=preflight.revision,
                    lock_timeout=timeout,
                )
            _require_confirmation(dependencies, settings, preflight)
            return method(
                identity,
                expected_revision=preflight.revision,
                lock_timeout=timeout,
            )

        return _with_project(dependencies, operation)

    _run(dependencies, settings, action, render_resource)


def _require_confirmation(
    dependencies: PresentationDependencies,
    settings: InvocationSettings,
    preflight: Any,
) -> None:
    stdout = dependencies.stdout()
    tty_mode = (
        not settings.json_output
        and dependencies.is_tty(dependencies.stdin())
        and dependencies.is_tty(stdout)
    )
    if not tty_mode:
        raise PresentationError(
            "confirmation_required",
            "This transition requires confirmation; rerun with --yes.",
            {"resource_id": preflight.id, "target_status": preflight.target_status.value},
        )

    stdout.write(f"Resource: {preflight.id}\n")
    stdout.write(f"Title: {preflight.title}\n")
    stdout.write(f"Requested status: {preflight.target_status.value}\n")
    if (
        preflight.target_status is ResourceStatus.DISMISSED
        and preflight.open_tasks > 0
    ):
        stdout.write(
            f"Warning: {preflight.open_tasks} open task(s) will become inactive.\n"
        )
    stdout.flush()
    if not dependencies.confirm("Proceed?"):
        raise PresentationError(
            "operation_cancelled",
            "Operation cancelled.",
            {"resource_id": preflight.id},
        )


def _with_project(
    dependencies: PresentationDependencies,
    operation: Callable[[WyrdApplication], T],
) -> T:
    application = dependencies.application_factory()
    application.discover_project(dependencies.cwd())
    return operation(application)


def _with_doctor_project(
    dependencies: PresentationDependencies,
    operation: Callable[[WyrdApplication], T],
) -> T:
    application = dependencies.application_factory()
    application.bind_doctor_project(dependencies.cwd())
    return operation(application)


def _run(
    dependencies: PresentationDependencies,
    settings: InvocationSettings,
    operation: Callable[[], T],
    human_renderer: Callable[..., None],
    *,
    doctor_result: bool = False,
) -> None:
    try:
        result = operation()
    except (ApplicationError, PresentationError) as error:
        fail_expected(
            error,
            json_output=settings.json_output,
            no_color=settings.no_color,
        )
    except Exception:
        fail_unexpected(
            json_output=settings.json_output,
            no_color=settings.no_color,
        )

    if settings.json_output:
        emit_json(result)
    else:
        stream = dependencies.stdout()
        human_renderer(
            result,
            stream=stream,
            styled=styling_enabled(
                stream,
                no_color=settings.no_color,
                tty=dependencies.is_tty(stream),
            ),
        )
    if doctor_result and not result.healthy:
        raise typer.Exit(1)
