"""Installed ``wyrd`` executable boundary."""

from __future__ import annotations

from wyrd_cli.bootstrap import create_cli


def main() -> None:
    create_cli()(prog_name="wyrd")


if __name__ == "__main__":  # pragma: no cover - module execution convenience
    main()
