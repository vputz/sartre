"""Shared, multi-writer backend: ``PostgresRegistry``.

A ``Registry`` backed by PostgreSQL for a shared, multi-writer manifest plane.
All query logic lives in :class:`~sartre._sql._SqlRegistry`; this subclass supplies
the Postgres dialect: a ``psycopg`` (v3) connection in autocommit mode and explicit
``transaction()`` blocks for write groups. The compare-and-swap ``set_pointer`` is a
single conditional write, atomic under concurrent writers. Requires the optional
``postgres`` extra (``psycopg``).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sartre._sql import _Conn, _SqlRegistry
from sartre.hashing import DEFAULT_HASHER, Hasher

try:
    import psycopg
except ImportError:  # pragma: no cover - exercised only without the extra
    psycopg = None  # type: ignore[assignment]


class PostgresRegistry(_SqlRegistry):
    """PostgreSQL-backed, shared/multi-writer implementation of the `Registry` port."""

    _PLACEHOLDER = "%s"
    _SEQ_TYPE = "BIGSERIAL PRIMARY KEY"
    _BLOB_TYPE = "BYTEA"

    def __init__(self, dsn: str, hasher: Hasher = DEFAULT_HASHER) -> None:
        if psycopg is None:
            raise RuntimeError(
                "PostgresRegistry requires the 'postgres' extra: pip install 'sartre[postgres]'"
            )
        self._dsn = dsn
        super().__init__(hasher)

    def _connect(self) -> _Conn:
        assert psycopg is not None  # guaranteed by __init__
        return psycopg.connect(self._dsn, autocommit=True)

    @contextmanager
    def _tx(self) -> Iterator[_Conn]:
        with self._lock, self._conn.transaction():  # type: ignore[attr-defined]
            yield self._conn
