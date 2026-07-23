"""The ``sartre`` command-line interface.

The pure core (:mod:`~sartre.cli.refs`, :mod:`~sartre.cli.config`,
:mod:`~sartre.cli.duration`, :mod:`~sartre.cli.ops`) is framework-free and importable
without Typer. Typer is loaded lazily by :func:`main` (the console-script entry point), so
``import sartre`` never requires the ``cli`` extra.
"""

from __future__ import annotations


def main() -> None:
    """Console-script entry point (``sartre``). Loads Typer lazily with a clear error."""
    try:
        from sartre.cli.app import app
    except ImportError as exc:  # the 'cli' extra (typer) is not installed
        raise SystemExit(
            "the sartre CLI requires the 'cli' extra — install it with: pip install 'sartre[cli]'"
        ) from exc
    app()
