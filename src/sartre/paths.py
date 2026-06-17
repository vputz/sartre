"""Canonical logical-path model (design decision D7).

A manifest path is simultaneously a logical join key (for diffing and blob
lookup) and a real filesystem path (for checkout/mount). ``normalize_path`` is
the single source of truth for the canonical form, applied at write time and by
any filesystem view. ``check_no_case_collisions`` enforces the manifest-level
invariant that makes every valid manifest checkout-safe on case-insensitive
filesystems.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from typing import Any

from sartre.errors import PathError

# Characters illegal on common filesystems (notably Windows/NTFS). Control
# characters and NUL are rejected separately and unconditionally.
_PORTABLE_ILLEGAL = frozenset('<>:"|?*')


def normalize_path(path: str, *, posix_only: bool = False) -> str:
    """Return the canonical form of a logical path, or raise :class:`PathError`.

    Coerced silently: Unicode → NFC, ``\\`` → ``/``, leading/trailing/duplicate
    slashes removed. Rejected: ``.``/``..`` segments (path traversal), NUL and
    control characters, and — unless ``posix_only`` — characters outside a
    conservative portable set and trailing dots/spaces on a segment.
    """
    result = unicodedata.normalize("NFC", path)
    result = result.replace("\\", "/")

    for ch in result:
        if ch == "\x00" or ord(ch) < 0x20:
            raise PathError(f"control character in path: {path!r}")

    segments = [seg for seg in result.split("/") if seg]
    if not segments:
        raise PathError(f"empty path: {path!r}")

    for seg in segments:
        if seg in (".", ".."):
            raise PathError(f"path traversal segment not allowed: {path!r}")
        if not posix_only:
            _check_portable(seg, path)

    return "/".join(segments)


def _check_portable(segment: str, path: str) -> None:
    illegal = _PORTABLE_ILLEGAL.intersection(segment)
    if illegal:
        raise PathError(f"illegal character(s) {sorted(illegal)} in path: {path!r}")
    if segment.endswith((" ", ".")):
        raise PathError(f"path segment may not end with a space or dot: {path!r}")


def check_no_case_collisions(paths: Iterable[str]) -> None:
    """Raise :class:`PathError` if any two distinct paths would collide on a
    case-insensitive filesystem.

    Walks the directory tree implied by the paths: at every level no two
    distinct components may be equal under case-folding, and no name may be used
    as both a file and a directory. This is the genuine checkout-safety check —
    stronger than whole-path equality, which would miss directory-level clashes
    (e.g. ``Foo/a`` vs ``foo/b``).
    """
    root: dict[str, Any] = {}
    for path in paths:
        node = root
        parts = path.split("/")
        for index, part in enumerate(parts):
            is_leaf = index == len(parts) - 1
            key = part.casefold()
            existing = node.get(key)
            if existing is None:
                existing = {"actual": part, "is_file": False, "is_dir": False, "children": {}}
                node[key] = existing
            elif existing["actual"] != part:
                raise PathError(
                    f"case-only path collision within manifest: "
                    f"{existing['actual']!r} and {part!r}"
                )
            if is_leaf:
                existing["is_file"] = True
            else:
                existing["is_dir"] = True
            if existing["is_file"] and existing["is_dir"]:
                raise PathError(
                    f"path used as both file and directory within manifest: "
                    f"{existing['actual']!r}"
                )
            node = existing["children"]
