"""Durable reference backend: ``SqliteRegistry``.

A ``Registry`` backed by a SQLite database, persisting the manifest plane —
manifests + their entries, pointers, and the append-only commit log — across
process restarts. Semantics mirror :class:`~sartre.memory.MemoryRegistry` (the
TLA-backed reference); equivalence is verified by differential property tests.

Mutations run in ``BEGIN IMMEDIATE`` transactions so the compare-and-swap
``set_pointer`` is atomic even against another writer. A process lock serializes
in-process access on the single connection. Leases are ephemeral coordination
state and are held in memory (process-scoped), never persisted.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator, Mapping, Sequence, Set
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sartre.errors import Conflict, NotFound
from sartre.hashing import DEFAULT_HASHER, Hasher, manifest_version
from sartre.model import HEAD, Alias, Coordinate, Entry, Hash, Head, Pin, Ref, Snapshot, Version
from sartre.ports import LeaseId, LogEntry

_SCHEMA = """
CREATE TABLE IF NOT EXISTS manifests (
    version    TEXT PRIMARY KEY,
    metadata   TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entries (
    version      TEXT NOT NULL,
    path         TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    size         INTEGER NOT NULL,
    inline       BLOB,
    PRIMARY KEY (version, path)
);
CREATE TABLE IF NOT EXISTS pointers (
    coord_name TEXT NOT NULL,
    coord_env  TEXT NOT NULL,
    name       TEXT NOT NULL,
    version    TEXT NOT NULL,
    PRIMARY KEY (coord_name, coord_env, name)
);
CREATE TABLE IF NOT EXISTS log (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    coord_name TEXT NOT NULL,
    coord_env  TEXT NOT NULL,
    version    TEXT NOT NULL,
    pointer    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class SqliteRegistry:
    """SQLite-backed, durable implementation of the `Registry` port."""

    def __init__(self, db_path: str | Path, hasher: Hasher = DEFAULT_HASHER) -> None:
        self._hasher = hasher
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._lock = threading.RLock()
        self._leases: dict[LeaseId, tuple[Version, frozenset[Hash]]] = {}
        self._next_lease = 0
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """A serialized ``BEGIN IMMEDIATE`` transaction (rollback on error)."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    # --- helpers ---

    @staticmethod
    def _pointer_name(ref: Ref) -> str:
        if isinstance(ref, Head):
            return "head"
        if isinstance(ref, Alias):
            return ref.name
        raise TypeError(f"not a pointer ref: {ref!r}")

    def _resolve_ref(self, coord: Coordinate, ref: Ref) -> Version:
        if isinstance(ref, Pin):
            row = self._conn.execute(
                "SELECT 1 FROM log WHERE coord_name=? AND coord_env=? AND version=? LIMIT 1",
                (coord.name, coord.env, ref.version),
            ).fetchone()
            if row is None:
                raise NotFound(f"version {ref.version} not known for {coord}")
            return ref.version
        name = self._pointer_name(ref)
        row = self._conn.execute(
            "SELECT version FROM pointers WHERE coord_name=? AND coord_env=? AND name=?",
            (coord.name, coord.env, name),
        ).fetchone()
        if row is None:
            raise NotFound(f"pointer {name!r} not set for {coord}")
        return row[0]

    # --- Registry port: read surface ---

    def head(self, coord: Coordinate, ref: Ref = HEAD) -> Version:
        with self._lock:
            return self._resolve_ref(coord, ref)

    def resolve(self, coord: Coordinate, ref: Ref = HEAD) -> Snapshot:
        with self._lock:
            version = self._resolve_ref(coord, ref)
            manifest = self._conn.execute(
                "SELECT metadata, created_at FROM manifests WHERE version=?", (version,)
            ).fetchone()
            entries = tuple(
                Entry(path=r[0], content_hash=r[1], size=r[2], inline=r[3])
                for r in self._conn.execute(
                    "SELECT path, content_hash, size, inline FROM entries "
                    "WHERE version=? ORDER BY path",
                    (version,),
                )
            )
            return Snapshot(
                coord=coord,
                version=version,
                created_at=datetime.fromisoformat(manifest[1]),
                metadata=json.loads(manifest[0]),
                entries=entries,
            )

    def list_pointers(self, coord: Coordinate) -> Mapping[str, Version]:
        with self._lock:
            return {
                r[0]: r[1]
                for r in self._conn.execute(
                    "SELECT name, version FROM pointers WHERE coord_name=? AND coord_env=?",
                    (coord.name, coord.env),
                )
            }

    def list_versions(self, coord: Coordinate) -> Sequence[Version]:
        with self._lock:
            seen: dict[Version, None] = {}
            for (version,) in self._conn.execute(
                "SELECT version FROM log WHERE coord_name=? AND coord_env=? ORDER BY seq",
                (coord.name, coord.env),
            ):
                seen.setdefault(version, None)
            return list(seen)

    def commit(
        self, coord: Coordinate, entries: Iterable[Entry], metadata: Mapping[str, Any]
    ) -> Version:
        materialized = tuple(entries)
        version = manifest_version(materialized, self._hasher)
        with self._tx() as conn:
            exists = conn.execute("SELECT 1 FROM manifests WHERE version=?", (version,)).fetchone()
            if exists is None:  # content-idempotent; first commit wins
                conn.execute(
                    "INSERT INTO manifests(version, metadata, created_at) VALUES (?, ?, ?)",
                    (version, json.dumps(dict(metadata)), datetime.now(UTC).isoformat()),
                )
                conn.executemany(
                    "INSERT INTO entries(version, path, content_hash, size, inline) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [(version, e.path, e.content_hash, e.size, e.inline) for e in materialized],
                )
        return version

    def set_pointer(
        self, coord: Coordinate, name: str, version: Version, *, expected: Version | None
    ) -> None:
        with self._tx() as conn:
            manifest = conn.execute(
                "SELECT 1 FROM manifests WHERE version=?", (version,)
            ).fetchone()
            if manifest is None:
                raise NotFound(f"cannot point at uncommitted version {version}")
            row = conn.execute(
                "SELECT version FROM pointers WHERE coord_name=? AND coord_env=? AND name=?",
                (coord.name, coord.env, name),
            ).fetchone()
            current = row[0] if row else None
            if current != expected:
                raise Conflict(
                    f"pointer {name!r} for {coord} is {current}, expected {expected}"
                )
            if row is None:
                conn.execute(
                    "INSERT INTO pointers(coord_name, coord_env, name, version) "
                    "VALUES (?, ?, ?, ?)",
                    (coord.name, coord.env, name, version),
                )
            else:
                conn.execute(
                    "UPDATE pointers SET version=? WHERE coord_name=? AND coord_env=? AND name=?",
                    (version, coord.name, coord.env, name),
                )
            conn.execute(
                "INSERT INTO log(coord_name, coord_env, version, pointer, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (coord.name, coord.env, version, name, datetime.now(UTC).isoformat()),
            )

    # --- Registry port: enumeration & lifecycle ---

    def list_coordinates(self) -> Sequence[Coordinate]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT coord_name, coord_env FROM pointers "
                "UNION SELECT coord_name, coord_env FROM log"
            )
            return [Coordinate(name, env) for (name, env) in rows]

    def list_log(self, coord: Coordinate) -> Sequence[LogEntry]:
        with self._lock:
            return [
                LogEntry(version=r[0], seq=r[1], created_at=datetime.fromisoformat(r[2]))
                for r in self._conn.execute(
                    "SELECT version, seq, created_at FROM log "
                    "WHERE coord_name=? AND coord_env=? ORDER BY seq",
                    (coord.name, coord.env),
                )
            ]

    def drop_version(self, version: Version) -> None:
        with self._tx() as conn:
            target = conn.execute(
                "SELECT coord_name, coord_env FROM pointers WHERE version=? LIMIT 1", (version,)
            ).fetchone()
            if target is not None:
                coord = Coordinate(target[0], target[1])
                raise Conflict(f"cannot drop {version}: still a pointer target of {coord}")
            conn.execute("DELETE FROM log WHERE version=?", (version,))
            conn.execute("DELETE FROM entries WHERE version=?", (version,))
            conn.execute("DELETE FROM manifests WHERE version=?", (version,))  # idempotent

    # --- Registry port: lease surface (in-memory, process-scoped) ---

    def acquire_lease(self, version: Version, hashes: Set[Hash]) -> LeaseId:
        with self._lock:
            lease_id = LeaseId(self._next_lease)
            self._next_lease += 1
            self._leases[lease_id] = (version, frozenset(hashes))
            return lease_id

    def release_lease(self, lease_id: LeaseId) -> None:
        with self._lock:
            self._leases.pop(lease_id, None)

    def active_leased_hashes(self) -> set[Hash]:
        with self._lock:
            return {h for _version, hashes in self._leases.values() for h in hashes}

    def active_leased_versions(self) -> set[Version]:
        with self._lock:
            return {version for version, _hashes in self._leases.values()}
