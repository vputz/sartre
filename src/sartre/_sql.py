"""Shared SQL implementation of the `Registry` port.

`_SqlRegistry` holds all query logic (schema, reads, ``commit``, ``set_pointer``,
``drop_version``, enumeration) and the in-memory, process-scoped lease map.
Concrete backends (:class:`~sartre.sqlite.SqliteRegistry`,
:class:`~sartre.postgres.PostgresRegistry`) supply only a small dialect seam:
the parameter placeholder, the ``seq``/``inline`` column types, a connection, and
a transaction context manager. Both DB-API drivers (``sqlite3``, ``psycopg``)
expose the same ``execute``/``fetchone``/``rowcount`` surface used here.

``set_pointer`` performs its compare-and-swap as a single conditional write, so it
is atomic under concurrent multi-writer contention with no read-modify-write window.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable, Iterator, Mapping, Sequence, Set
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sartre.errors import Conflict, NotFound
from sartre.hashing import DEFAULT_HASHER, Hasher, manifest_version
from sartre.model import HEAD, Alias, Coordinate, Entry, Hash, Head, Pin, Ref, Snapshot, Version
from sartre.ports import DEFAULT_LEASE_TTL, LeaseId, LogEntry, PointerMove

# A DB-API connection (sqlite3 or psycopg); typed loosely since the two drivers'
# overloaded signatures don't share a nominal protocol. Only execute/cursor are used.
_Conn = Any


class _SqlRegistry:
    """Dialect-agnostic SQL implementation of the `Registry` port."""

    _PLACEHOLDER = "?"
    _SEQ_TYPE = "INTEGER PRIMARY KEY AUTOINCREMENT"
    _BLOB_TYPE = "BLOB"
    # A SQL scalar expression giving the registry's current time as epoch seconds. All
    # lease-expiry arithmetic and comparison uses this — the *registry's* clock, never a
    # client's — so writers and GC hosts with skewed local clocks still agree.
    _NOW_SQL = "(julianday('now') - 2440587.5) * 86400.0"
    _TS_TYPE = "REAL"  # column type for an epoch-seconds timestamp

    def __init__(self, hasher: Hasher = DEFAULT_HASHER) -> None:
        self._hasher = hasher
        self._lock = threading.RLock()
        self._conn = self._connect()
        with self._lock:
            for statement in self._schema():
                self._exec(self._conn, statement)

    # --- dialect seam (subclasses override) ---

    def _connect(self) -> _Conn:
        raise NotImplementedError

    @contextmanager
    def _tx(self) -> Iterator[_Conn]:
        raise NotImplementedError
        yield  # pragma: no cover  (makes this a generator for the type checker)

    def close(self) -> None:
        with self._lock:
            self._conn.close()  # type: ignore[attr-defined]

    def _schema(self) -> list[str]:
        return [
            "CREATE TABLE IF NOT EXISTS manifests ("
            "version TEXT PRIMARY KEY, metadata TEXT NOT NULL, created_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS entries ("
            "version TEXT NOT NULL, path TEXT NOT NULL, content_hash TEXT NOT NULL, "
            f"size INTEGER NOT NULL, inline {self._BLOB_TYPE}, PRIMARY KEY (version, path))",
            "CREATE TABLE IF NOT EXISTS pointers ("
            "coord_name TEXT NOT NULL, coord_env TEXT NOT NULL, name TEXT NOT NULL, "
            "version TEXT NOT NULL, PRIMARY KEY (coord_name, coord_env, name))",
            f"CREATE TABLE IF NOT EXISTS log (seq {self._SEQ_TYPE}, "
            "coord_name TEXT NOT NULL, coord_env TEXT NOT NULL, version TEXT NOT NULL, "
            "pointer TEXT NOT NULL, created_at TEXT NOT NULL, "
            "actor TEXT NOT NULL DEFAULT 'unknown', reason TEXT)",
            f"CREATE TABLE IF NOT EXISTS pointer_moves (move_seq {self._SEQ_TYPE}, "
            "coord_name TEXT NOT NULL, coord_env TEXT NOT NULL, pointer TEXT NOT NULL, "
            "from_version TEXT, to_version TEXT NOT NULL, "
            "actor TEXT NOT NULL, reason TEXT, at TEXT NOT NULL)",
            f"CREATE TABLE IF NOT EXISTS leases (lease_id {self._SEQ_TYPE}, "
            f"version TEXT NOT NULL, hashes TEXT NOT NULL, expires_at {self._TS_TYPE} NOT NULL)",
        ]

    # --- execution helpers ---

    def _q(self, sql: str) -> str:
        return sql if self._PLACEHOLDER == "?" else sql.replace("?", self._PLACEHOLDER)

    def _exec(self, conn: _Conn, sql: str, params: Sequence[Any] = ()) -> Any:
        return conn.execute(self._q(sql), params)

    def _exec_many(self, conn: _Conn, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        cur = conn.cursor()
        cur.executemany(self._q(sql), list(seq))

    @staticmethod
    def _pointer_name(ref: Ref) -> str:
        if isinstance(ref, Head):
            return "head"
        if isinstance(ref, Alias):
            return ref.name
        raise TypeError(f"not a pointer ref: {ref!r}")

    def _resolve_ref(self, coord: Coordinate, ref: Ref) -> Version:
        if isinstance(ref, Pin):
            row = self._exec(
                self._conn,
                "SELECT 1 FROM log WHERE coord_name=? AND coord_env=? AND version=? LIMIT 1",
                (coord.name, coord.env, ref.version),
            ).fetchone()
            if row is None:
                raise NotFound(f"version {ref.version} not known for {coord}")
            return ref.version
        name = self._pointer_name(ref)
        row = self._exec(
            self._conn,
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
            manifest = self._exec(
                self._conn, "SELECT metadata, created_at FROM manifests WHERE version=?", (version,)
            ).fetchone()
            entries = tuple(
                Entry(
                    path=r[0],
                    content_hash=r[1],
                    size=r[2],
                    inline=bytes(r[3]) if r[3] is not None else None,
                )
                for r in self._exec(
                    self._conn,
                    "SELECT path, content_hash, size, inline FROM entries "
                    "WHERE version=? ORDER BY path",
                    (version,),
                ).fetchall()
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
            rows = self._exec(
                self._conn,
                "SELECT name, version FROM pointers WHERE coord_name=? AND coord_env=?",
                (coord.name, coord.env),
            ).fetchall()
            return {r[0]: r[1] for r in rows}

    def list_versions(self, coord: Coordinate) -> Sequence[Version]:
        with self._lock:
            seen: dict[Version, None] = {}
            for row in self._exec(
                self._conn,
                "SELECT version FROM log WHERE coord_name=? AND coord_env=? ORDER BY seq",
                (coord.name, coord.env),
            ).fetchall():
                seen.setdefault(row[0], None)
            return list(seen)

    def list_coordinates(self) -> Sequence[Coordinate]:
        with self._lock:
            rows = self._exec(
                self._conn,
                "SELECT coord_name, coord_env FROM pointers "
                "UNION SELECT coord_name, coord_env FROM log",
            ).fetchall()
            return [Coordinate(r[0], r[1]) for r in rows]

    def list_log(self, coord: Coordinate) -> Sequence[LogEntry]:
        with self._lock:
            rows = self._exec(
                self._conn,
                "SELECT version, seq, created_at, actor, reason FROM log "
                "WHERE coord_name=? AND coord_env=? ORDER BY seq",
                (coord.name, coord.env),
            ).fetchall()
            return [
                LogEntry(
                    version=r[0],
                    seq=r[1],
                    created_at=datetime.fromisoformat(r[2]),
                    actor=r[3],
                    reason=r[4],
                )
                for r in rows
            ]

    def list_pointer_history(self, coord: Coordinate) -> Sequence[PointerMove]:
        with self._lock:
            rows = self._exec(
                self._conn,
                "SELECT pointer, from_version, to_version, actor, reason, at "
                "FROM pointer_moves WHERE coord_name=? AND coord_env=? ORDER BY move_seq",
                (coord.name, coord.env),
            ).fetchall()
            return [
                PointerMove(
                    name=r[0],
                    from_version=r[1],
                    to_version=r[2],
                    actor=r[3],
                    reason=r[4],
                    at=datetime.fromisoformat(r[5]),
                )
                for r in rows
            ]

    # --- Registry port: write surface ---

    def commit(
        self, coord: Coordinate, entries: Iterable[Entry], metadata: Mapping[str, Any]
    ) -> Version:
        materialized = tuple(entries)
        version = manifest_version(materialized, self._hasher)
        with self._tx() as conn:
            exists = self._exec(
                conn, "SELECT 1 FROM manifests WHERE version=?", (version,)
            ).fetchone()
            if exists is None:  # content-idempotent; first commit wins
                self._exec(
                    conn,
                    "INSERT INTO manifests(version, metadata, created_at) VALUES (?, ?, ?)",
                    (version, json.dumps(dict(metadata)), datetime.now(UTC).isoformat()),
                )
                self._exec_many(
                    conn,
                    "INSERT INTO entries(version, path, content_hash, size, inline) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [(version, e.path, e.content_hash, e.size, e.inline) for e in materialized],
                )
        return version

    def set_pointer(
        self,
        coord: Coordinate,
        name: str,
        version: Version,
        *,
        expected: Version | None,
        actor: str = "unknown",
        reason: str | None = None,
    ) -> None:
        with self._tx() as conn:
            if self._exec(
                conn, "SELECT 1 FROM manifests WHERE version=?", (version,)
            ).fetchone() is None:
                raise NotFound(f"cannot point at uncommitted version {version}")
            if expected is None:  # must not already exist
                cur = self._exec(
                    conn,
                    "INSERT INTO pointers(coord_name, coord_env, name, version) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT (coord_name, coord_env, name) DO NOTHING",
                    (coord.name, coord.env, name, version),
                )
            else:  # advance only if the stored value still equals `expected`
                cur = self._exec(
                    conn,
                    "UPDATE pointers SET version=? "
                    "WHERE coord_name=? AND coord_env=? AND name=? AND version=?",
                    (version, coord.name, coord.env, name, expected),
                )
            if cur.rowcount != 1:  # conditional write matched nothing -> lost the race
                raise Conflict(
                    f"pointer {name!r} for {coord} did not match expected {expected}"
                )
            now = datetime.now(UTC).isoformat()
            self._exec(  # tip event: stamps who/why on the commit-log row
                conn,
                "INSERT INTO log(coord_name, coord_env, version, pointer, created_at, "
                "actor, reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (coord.name, coord.env, version, name, now, actor, reason),
            )
            self._exec(  # append-only provenance audit; from_version = prior value (expected)
                conn,
                "INSERT INTO pointer_moves(coord_name, coord_env, pointer, from_version, "
                "to_version, actor, reason, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (coord.name, coord.env, name, expected, version, actor, reason, now),
            )

    def drop_version(self, version: Version) -> None:
        with self._tx() as conn:
            target = self._exec(
                conn,
                "SELECT coord_name, coord_env FROM pointers WHERE version=? LIMIT 1",
                (version,),
            ).fetchone()
            if target is not None:
                raise Conflict(
                    f"cannot drop {version}: still a pointer target of "
                    f"{Coordinate(target[0], target[1])}"
                )
            self._exec(conn, "DELETE FROM log WHERE version=?", (version,))
            self._exec(conn, "DELETE FROM entries WHERE version=?", (version,))
            self._exec(conn, "DELETE FROM manifests WHERE version=?", (version,))  # idempotent

    # --- Registry port: lease surface (durable, registry-clock TTL) ---

    def acquire_lease(
        self, version: Version, hashes: Set[Hash], ttl: float = DEFAULT_LEASE_TTL
    ) -> LeaseId:
        with self._tx() as conn:
            row = self._exec(
                conn,
                "INSERT INTO leases(version, hashes, expires_at) "
                f"VALUES (?, ?, {self._NOW_SQL} + ?) RETURNING lease_id",
                (version, json.dumps(sorted(hashes)), ttl),
            ).fetchone()
            return LeaseId(int(row[0]))

    def renew_lease(self, lease_id: LeaseId, ttl: float = DEFAULT_LEASE_TTL) -> bool:
        with self._tx() as conn:
            cur = self._exec(
                conn,
                f"UPDATE leases SET expires_at = {self._NOW_SQL} + ? "
                f"WHERE lease_id = ? AND expires_at > {self._NOW_SQL}",
                (ttl, lease_id),
            )
            return cur.rowcount == 1

    def release_lease(self, lease_id: LeaseId) -> None:
        with self._tx() as conn:
            self._exec(conn, "DELETE FROM leases WHERE lease_id = ?", (lease_id,))

    def active_leased_hashes(self) -> set[Hash]:
        with self._lock:
            rows = self._exec(
                self._conn, f"SELECT hashes FROM leases WHERE expires_at > {self._NOW_SQL}"
            ).fetchall()
            return {h for r in rows for h in json.loads(r[0])}

    def active_leased_versions(self) -> set[Version]:
        with self._lock:
            rows = self._exec(
                self._conn, f"SELECT version FROM leases WHERE expires_at > {self._NOW_SQL}"
            ).fetchall()
            return {r[0] for r in rows}
