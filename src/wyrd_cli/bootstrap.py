"""Production composition root; the only module wiring all architectural layers."""

from __future__ import annotations

import typer

from wyrd_cli.application import SystemClock, WyrdApplication
from wyrd_cli.infrastructure import create_filesystem_storage
from wyrd_cli.presentation import PresentationDependencies, create_app


def create_application() -> WyrdApplication:
    """Create one unbound production application for one CLI invocation."""

    return WyrdApplication(create_filesystem_storage(), SystemClock())


def create_cli() -> typer.Typer:
    """Compose the production Typer application without touching project state."""

    return create_app(PresentationDependencies(application_factory=create_application))
