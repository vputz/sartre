"""Reference grammar (framework-free): parse/render the OCI-lineage CLI reference token.

A reference is ``name/env`` optionally followed by ``:alias`` (a mutable pointer) or
``@version`` (an immutable pin); bare ``name/env`` is head. Env may be elided when a
default env is supplied (``name`` → ``name/<default>``). Parsing is total and
``parse_ref(render_ref(c, r), default_env=…) == (c, r)`` for every coordinate/ref.
"""

from __future__ import annotations

import re

from sartre.cli.errors import CliError
from sartre.model import Alias, Coordinate, Head, Pin, Ref

# name / env / alias segments: a conservative, unambiguous charset (no '/', ':', '@').
_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# a content-hash version key, e.g. "sha256:<hex>"
_VERSION = re.compile(r"[A-Za-z0-9]+:[0-9a-fA-F]+")


def _segment(value: str, what: str) -> str:
    if not _SEGMENT.fullmatch(value):
        raise CliError(f"invalid {what} {value!r} (use letters, digits, '.', '-', '_')")
    return value


def parse_coord(token: str, *, default_env: str | None = None) -> Coordinate:
    """Parse a bare ``name/env`` (or ``name`` with a default env) into a Coordinate."""
    if "/" in token:
        name, _, env = token.partition("/")
        return Coordinate(_segment(name, "name"), _segment(env, "env"))
    if default_env is None:
        raise CliError(f"reference {token!r} needs an env: write 'name/env' or set a default env")
    return Coordinate(_segment(token, "name"), default_env)


def parse_ref(token: str, *, default_env: str | None = None) -> tuple[Coordinate, Ref]:
    """Parse a full reference token into ``(Coordinate, Ref)``.

    ``@`` → :class:`Pin` (everything after ``@`` is the version; its internal ``:`` is not a
    delimiter); else ``:`` → :class:`Alias`; else :class:`Head`.
    """
    if "@" in token:  # pin: coord@version  (version keeps its internal ':')
        coord_part, _, version = token.partition("@")
        if not _VERSION.fullmatch(version):
            raise CliError(f"invalid version {version!r} (expected 'algo:digest')")
        return parse_coord(coord_part, default_env=default_env), Pin(version)
    if ":" in token:  # alias: coord:alias
        coord_part, _, alias = token.partition(":")
        return parse_coord(coord_part, default_env=default_env), Alias(_segment(alias, "alias"))
    return parse_coord(token, default_env=default_env), Head()


def parse_source(coord: Coordinate, token: str) -> Ref:
    """Parse the *source* of a ``point`` within an already-known coordinate.

    A version key (``algo:digest``) → :class:`Pin`; ``head`` → :class:`Head`; any other bare
    name → :class:`Alias` (point at wherever that pointer currently is).
    """
    del coord  # source is resolved within the caller's coordinate; kept for symmetry
    if _VERSION.fullmatch(token):
        return Pin(token)
    if token == "head":
        return Head()
    return Alias(_segment(token, "pointer"))


def pointer_name(ref: Ref) -> str:
    """The stored pointer name for a mutable ref (``head`` for Head, the alias otherwise)."""
    if isinstance(ref, Head):
        return "head"
    if isinstance(ref, Alias):
        return ref.name
    raise CliError("a pin is not a mutable pointer; target head or an alias")


def render_coord(coord: Coordinate) -> str:
    return f"{coord.name}/{coord.env}"


def render_ref(coord: Coordinate, ref: Ref) -> str:
    """Inverse of :func:`parse_ref` — ``parse_ref(render_ref(c, r)) == (c, r)``."""
    base = render_coord(coord)
    if isinstance(ref, Head):
        return base
    if isinstance(ref, Alias):
        return f"{base}:{ref.name}"
    return f"{base}@{ref.version}"  # Pin — the only remaining variant of the sealed Ref union
