"""Durable single-node backend: ``SqliteRegistry``.

A ``Registry`` backed by a SQLite database, persisting the manifest plane —
manifests + their entries, pointers, and the append-only commit log — across
process restarts. All query logic lives in :class:`~sartre._sql._SqlRegistry`;
this subclass supplies the SQLite dialect: a ``sqlite3`` connection (autocommit)
and ``BEGIN IMMEDIATE`` transactions so the compare-and-swap ``set_pointer`` is
atomic even against another writer. Semantics mirror
:class:`~sartre.memory.MemoryRegistry` (the TLA-backed reference); equivalence is
verified by differential property tests.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sartre._sql import _Conn, _SqlRegistry
from sartre.hashing import DEFAULT_HASHER, Hasher


class SqliteRegistry(_SqlRegistry):
    """SQLite-backed, durable implementation of the `Registry` port."""

    _PLACEHOLDER = "?"
    _SEQ_TYPE = "INTEGER PRIMARY KEY AUTOINCREMENT"
    _BLOB_TYPE = "BLOB"

    def __init__(self, db_path: str | Path, hasher: Hasher = DEFAULT_HASHER) -> None:
        self._db_path = str(db_path)
        super().__init__(hasher)

    def _connect(self) -> _Conn:
        return sqlite3.connect(self._db_path, check_same_thread=False, isolation_level=None)

    def _migrate(self, conn: _Conn) -> None:
        """Relax `pointer_moves.to_version` to nullable in a pre-existing database.

        SQLite has no ``ALTER COLUMN … DROP NOT NULL``, so rebuild the table (create-new →
        copy → swap) only when the column is still ``NOT NULL``. Rows are preserved; the
        rebuild is wrapped in a transaction so a crash cannot leave a half-migrated schema.
        """
        info = conn.execute("PRAGMA table_info(pointer_moves)").fetchall()
        # PRAGMA columns: (cid, name, type, notnull, dflt_value, pk)
        if not any(c[1] == "to_version" and c[3] == 1 for c in info):
            return  # already nullable (fresh DB or already migrated)
        create = next(s for s in self._schema() if "CREATE TABLE IF NOT EXISTS pointer_moves" in s)
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DROP TABLE IF EXISTS pointer_moves_old")
            conn.execute("ALTER TABLE pointer_moves RENAME TO pointer_moves_old")
            conn.execute(create)  # recreates with nullable to_version
            conn.execute(
                "INSERT INTO pointer_moves(move_seq, coord_name, coord_env, pointer, "
                "from_version, to_version, actor, reason, at) "
                "SELECT move_seq, coord_name, coord_env, pointer, from_version, to_version, "
                "actor, reason, at FROM pointer_moves_old"
            )
            conn.execute("DROP TABLE pointer_moves_old")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    @contextmanager
    def _tx(self) -> Iterator[_Conn]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
