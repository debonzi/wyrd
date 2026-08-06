"""Error, exit-status, and raw-argv boundaries for the Typer application."""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from typing import Any, NoReturn

import typer
from typer._click.exceptions import ClickException, UsageError
from typer.core import TyperGroup

from wyrd_cli.domain.errors import ApplicationError

from .serialization import dumps, error_envelope


class PresentationError(Exception):
    """Expected input/confirmation failure produced by the CLI adapter."""

    def __init__(
        self,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
        *,
        exit_code: int = 1,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})
        self.exit_code = exit_code


class JsonAwareTyperGroup(TyperGroup):
    """Render Click parser failures as JSON when raw argv enables JSON mode."""

    def main(
        self,
        args: Sequence[str] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: bool = True,
        windows_expand_args: bool = True,
        **extra: Any,
    ) -> Any:
        raw_args = list(sys.argv[1:] if args is None else args)
        if "--json" not in raw_args:
            return super().main(
                args=raw_args,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=standalone_mode,
                windows_expand_args=windows_expand_args,
                **extra,
            )

        try:
            result = super().main(
                args=raw_args,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=False,
                windows_expand_args=windows_expand_args,
                **extra,
            )
        except UsageError as error:
            typer.echo(
                dumps(error_envelope("usage_error", error.format_message(), {})),
                err=True,
            )
            if standalone_mode:
                raise SystemExit(2) from error
            return 2
        except ClickException as error:
            typer.echo(
                dumps(error_envelope("usage_error", error.format_message(), {})),
                err=True,
            )
            if standalone_mode:
                raise SystemExit(error.exit_code) from error
            return error.exit_code

        # Typer returns explicit Exit codes when called with standalone_mode=False.
        if standalone_mode and isinstance(result, int) and result != 0:
            raise SystemExit(result)
        return result


def fail_expected(error: ApplicationError | PresentationError, *, json_output: bool, no_color: bool) -> NoReturn:
    """Emit exactly one ordinary error and terminate with its mapped status."""

    del no_color  # Human errors intentionally remain readable plain text.
    code = error.code
    message = error.message
    details = error.details
    exit_code = error.exit_code if isinstance(error, PresentationError) else (
        2 if code == "usage_error" else 1
    )
    if json_output:
        typer.echo(dumps(error_envelope(code, message, details)), err=True)
    else:
        typer.echo(f"Error [{code}]: {message}", err=True)
    raise typer.Exit(exit_code)


def fail_unexpected(*, json_output: bool, no_color: bool) -> NoReturn:
    """Hide implementation details for an unexpected failure."""

    fail_expected(
        PresentationError(
            "internal_error",
            "An unexpected internal error occurred.",
            {},
        ),
        json_output=json_output,
        no_color=no_color,
    )
