"""Domain model: coordinates, refs, entries, snapshots (design memo §3).

An artifact is a named, versioned *manifest of entries*. A ``Version`` is
immutable; a ``Ref`` selects which version of a ``Coordinate`` is addressed; an
``Entry`` is one logical file whose bytes are fetched lazily by content hash.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

# Self-describing content hash, ``"<algo>:<digest>"`` (see sartre.hashing).
type Hash = str

# Opaque immutable version identifier. Consumers MUST NOT parse or order it by
# structure; the concrete format is deliberately not fixed by this capability.
type Version = str


@dataclass(frozen=True, slots=True)
class Coordinate:
    """Identifies an artifact by logical ``name`` within an environment ``env``.

    ``env`` lives in the coordinate (not the ref), so ``Head``/``Alias``/``Pin``
    mean the same thing in every environment and each ``(name, env)`` is an
    independent pointer lineage.
    """

    name: str
    env: str


@dataclass(frozen=True, slots=True)
class Head:
    """The mutable tip — the env HEAD / default ``latest`` pointer."""


@dataclass(frozen=True, slots=True)
class Alias:
    """A named mutable pointer, e.g. ``production`` or ``best``."""

    name: str


@dataclass(frozen=True, slots=True)
class Pin:
    """An immutable pin to an explicit version id."""

    version: Version


# Sealed union of the three ways to address a version of a coordinate.
type Ref = Head | Alias | Pin

# The default ref: the mutable tip. A shared singleton (Head is immutable) so it
# can be used safely as a default argument value.
HEAD: Head = Head()


@dataclass(frozen=True, slots=True)
class Entry:
    """One logical file in a manifest: a path, its content hash, and size.

    ``inline`` MAY carry the bytes of a small file directly in the manifest row
    (design memo §6) so the common all-metadata catalog read needs no blob
    fetch; large blobs leave it ``None`` and are fetched by ``content_hash``.
    """

    path: str
    content_hash: Hash
    size: int
    inline: bytes | None = None


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A resolved, immutable version: its manifest plus provenance metadata.

    Producing a snapshot fetches no heavy blob bytes — only the manifest
    (paths, hashes, sizes) and free-form ``metadata`` (date ranges, provenance).
    """

    coord: Coordinate
    version: Version
    created_at: datetime
    metadata: Mapping[str, Any]
    entries: tuple[Entry, ...]
