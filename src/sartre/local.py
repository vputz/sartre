"""``open_local``: a persistent single-node repository in one directory.

Wires a durable :class:`~sartre.sqlite.SqliteRegistry` (manifest plane) and a
content-addressed :class:`~sartre.store.CasStore` over the local filesystem (blob
plane) into a :class:`~sartre.repository.Repository`. Reopening the same path
recovers previously published state.
"""

from __future__ import annotations

from pathlib import Path

import fsspec

from sartre.repository import Repository
from sartre.sqlite import SqliteRegistry
from sartre.store import CasStore, FsspecBlobBackend


def open_local(path: str | Path) -> Repository:
    """Open (creating if needed) a persistent repository rooted at ``path``.

    Layout: ``{path}/registry.db`` (SQLite manifest plane) and ``{path}/blobs``
    (content-addressed local blob plane). Reopening the same ``path`` recovers the
    repository's published pointers, versions, manifests, and blobs.
    """
    root = Path(path)
    root.mkdir(parents=True, exist_ok=True)
    registry = SqliteRegistry(root / "registry.db")
    store = CasStore(FsspecBlobBackend(fsspec.filesystem("file"), root=str(root / "blobs")))
    return Repository(registry, store)
