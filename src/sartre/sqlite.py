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
