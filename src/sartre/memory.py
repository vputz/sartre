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
from collections.abc import Iterable, Mapping, Sequence, Set
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sartre.errors import Conflict, NotFound
from sartre.hashing import DEFAULT_HASHER, Hasher, manifest_version
from sartre.model import HEAD, Alias, Coordinate, Entry, Hash, Head, Pin, Ref, Snapshot, Version
from sartre.ports import LeaseId, LogEntry


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
        self._leases: dict[LeaseId, tuple[Version, frozenset[Hash]]] = {}
        self._next_lease = 0
        self._lock = threading.Lock()

    # --- helpers (call under self._lock) ---

    def _state(self, coord: Coordinate) -> _CoordState:
        """Get-or-create — for write paths that legitimately materialize a coordinate."""
        return self._coords.setdefault((coord.name, coord.env), _CoordState())

    def _peek(self, coord: Coordinate) -> _CoordState | None:
        """Read-only lookup — never materializes an empty coordinate."""
        return self._coords.get((coord.name, coord.env))

    def _pointer_name(self, ref: Ref) -> str:
        if isinstance(ref, Head):
            return "head"
        if isinstance(ref, Alias):
            return ref.name
        raise TypeError(f"not a pointer ref: {ref!r}")

    def _resolve_ref(self, coord: Coordinate, ref: Ref) -> Version:
        state = self._peek(coord)
        if state is None:
            raise NotFound(f"nothing published for {coord}")
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
            state = self._peek(coord)
            return dict(state.pointers) if state else {}

    def list_versions(self, coord: Coordinate) -> Sequence[Version]:
        with self._lock:
            state = self._peek(coord)
            seen: dict[Version, None] = {}  # distinct versions in commit-log order
            for entry in state.log if state else ():
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
            existing = self._peek(coord)  # don't materialize the coord on a conflict
            current = existing.pointers.get(name) if existing else None
            if current != expected:
                raise Conflict(
                    f"pointer {name!r} for {coord} is {current}, expected {expected}"
                )
            state = self._state(coord)  # committing to write → now materialize
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

    # --- enumeration & lifecycle (garbage collection) ---

    def list_coordinates(self) -> Sequence[Coordinate]:
        with self._lock:
            return [Coordinate(name, env) for (name, env) in self._coords]

    def list_log(self, coord: Coordinate) -> Sequence[LogEntry]:
        with self._lock:
            state = self._peek(coord)
            return [
                LogEntry(version=e.version, seq=e.seq, created_at=e.created_at)
                for e in (state.log if state else ())
            ]

    def drop_version(self, version: Version) -> None:
        with self._lock:
            for (name, env), state in self._coords.items():
                if version in state.pointers.values():
                    coord = Coordinate(name, env)
                    raise Conflict(f"cannot drop {version}: still a pointer target of {coord}")
            for state in self._coords.values():  # prune from every coordinate's log
                state.log = [e for e in state.log if e.version != version]
            self._manifests.pop(version, None)  # idempotent

    def acquire_lease(self, version: Version, hashes: Set[Hash]) -> LeaseId:
        with self._lock:
            lease_id = LeaseId(self._next_lease)
            self._next_lease += 1
            self._leases[lease_id] = (version, frozenset(hashes))
            return lease_id

    def release_lease(self, lease_id: LeaseId) -> None:
        with self._lock:
            self._leases.pop(lease_id, None)  # idempotent

    def active_leased_hashes(self) -> set[Hash]:
        with self._lock:
            return {h for _version, hashes in self._leases.values() for h in hashes}

    def active_leased_versions(self) -> set[Version]:
        with self._lock:
            return {version for version, _hashes in self._leases.values()}
