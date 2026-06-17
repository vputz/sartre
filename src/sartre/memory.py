"""In-memory reference backend: ``MemoryRegistry``.

An ephemeral, single-process ``Registry`` realizing the manifest plane with three
structures — content-addressed manifests (keyed by ``manifest_version``, so
``commit`` is idempotent and dedup is structural), a pointer table of current
tips (cheap ``head``), and an append-only per-coordinate commit log (``seq`` +
``created_at``). A lock makes mutations atomic in-process: the stand-in for a
transactional backend. Suitable for tests and local use; nothing is persisted.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sartre.errors import Conflict, NotFound
from sartre.hashing import DEFAULT_HASHER, Hasher, manifest_version
from sartre.model import HEAD, Alias, Coordinate, Entry, Head, Pin, Ref, Snapshot, Version


@dataclass(frozen=True)
class _ManifestRecord:
    entries: tuple[Entry, ...]
    metadata: Mapping[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class _LogEntry:
    seq: int
    version: Version
    created_at: datetime
    pointer: str


@dataclass
class _CoordState:
    pointers: dict[str, Version] = field(default_factory=dict)  # pointer name -> version
    log: list[_LogEntry] = field(default_factory=list)
    next_seq: int = 0


class MemoryRegistry:
    """In-memory implementation of the `Registry` port."""

    def __init__(self, hasher: Hasher = DEFAULT_HASHER) -> None:
        self._hasher = hasher
        self._manifests: dict[Version, _ManifestRecord] = {}
        self._coords: dict[tuple[str, str], _CoordState] = {}
        self._lock = threading.Lock()

    # --- helpers (call under self._lock) ---

    def _state(self, coord: Coordinate) -> _CoordState:
        return self._coords.setdefault((coord.name, coord.env), _CoordState())

    def _pointer_name(self, ref: Ref) -> str:
        if isinstance(ref, Head):
            return "head"
        if isinstance(ref, Alias):
            return ref.name
        raise TypeError(f"not a pointer ref: {ref!r}")

    def _resolve_ref(self, coord: Coordinate, ref: Ref) -> Version:
        state = self._state(coord)
        if isinstance(ref, Pin):
            if ref.version not in {e.version for e in state.log}:
                raise NotFound(f"version {ref.version} not known for {coord}")
            return ref.version
        name = self._pointer_name(ref)
        if name not in state.pointers:
            raise NotFound(f"pointer {name!r} not set for {coord}")
        return state.pointers[name]

    # --- Registry port ---

    def head(self, coord: Coordinate, ref: Ref = HEAD) -> Version:
        with self._lock:
            return self._resolve_ref(coord, ref)

    def resolve(self, coord: Coordinate, ref: Ref = HEAD) -> Snapshot:
        with self._lock:
            version = self._resolve_ref(coord, ref)
            record = self._manifests[version]
            return Snapshot(
                coord=coord,
                version=version,
                created_at=record.created_at,
                metadata=record.metadata,
                entries=record.entries,
            )

    def list_pointers(self, coord: Coordinate) -> Mapping[str, Version]:
        with self._lock:
            return dict(self._state(coord).pointers)

    def list_versions(self, coord: Coordinate) -> Sequence[Version]:
        with self._lock:
            seen: dict[Version, None] = {}  # distinct versions in commit-log order
            for entry in self._state(coord).log:
                seen.setdefault(entry.version, None)
            return list(seen)

    def commit(
        self, coord: Coordinate, entries: Iterable[Entry], metadata: Mapping[str, Any]
    ) -> Version:
        materialized = tuple(entries)
        version = manifest_version(materialized, self._hasher)
        with self._lock:
            if version not in self._manifests:  # content-idempotent; first commit wins
                self._manifests[version] = _ManifestRecord(
                    entries=materialized,
                    metadata=dict(metadata),
                    created_at=datetime.now(UTC),
                )
            return version

    def set_pointer(
        self, coord: Coordinate, name: str, version: Version, *, expected: Version | None
    ) -> None:
        with self._lock:
            if version not in self._manifests:
                raise NotFound(f"cannot point at uncommitted version {version}")
            state = self._state(coord)
            current = state.pointers.get(name)
            if current != expected:
                raise Conflict(
                    f"pointer {name!r} for {coord} is {current}, expected {expected}"
                )
            state.pointers[name] = version
            state.log.append(
                _LogEntry(
                    seq=state.next_seq,
                    version=version,
                    created_at=datetime.now(UTC),
                    pointer=name,
                )
            )
            state.next_seq += 1
