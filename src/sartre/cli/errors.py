"""A framework-free CLI error, so the pure core (refs/config/duration/ops) can signal
user-facing failures without importing Typer. The Typer layer catches it and exits."""

from __future__ import annotations


class CliError(Exception):
    """A user-facing CLI failure with a clean message (bad reference, unknown profile,
    malformed duration, …). The app renders its message and exits non-zero."""
