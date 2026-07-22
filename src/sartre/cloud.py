"""``open_cloud``: a shared repository over Postgres + an object-store blob plane.

Wires a :class:`~sartre.postgres.PostgresRegistry` (shared, multi-writer manifest
plane) and a content-addressed :class:`~sartre.store.CasStore` over the fsspec
filesystem named by ``blob_url`` (e.g. an ``s3://`` URL). With ``cache_dir`` set,
blob reads are served through a :class:`~sartre.store.CachingStore` backed by a
local on-disk cache over the remote store.
"""

from __future__ import annotations

from typing import Any

import fsspec

from sartre.postgres import PostgresRegistry
from sartre.repository import Repository
from sartre.store import CachingStore, CasStore, FsspecBlobBackend


def open_cloud(
    registry_dsn: str,
    blob_url: str,
    *,
    cache_dir: str | None = None,
    storage_options: dict[str, Any] | None = None,
) -> Repository:
    """Assemble a shared repository from a Postgres registry and an object-store blob plane.

    ``blob_url`` is any fsspec URL (``s3://bucket/prefix``, ``file:///data``, …). When
    ``cache_dir`` is given, a local on-disk cache fronts the remote blob store.
    """
    fs, root = fsspec.core.url_to_fs(blob_url, **(storage_options or {}))
    remote: CasStore | CachingStore = CasStore(FsspecBlobBackend(fs, root=root))
    if cache_dir is not None:
        local = CasStore(FsspecBlobBackend(fsspec.filesystem("file"), root=cache_dir))
        remote = CachingStore(local=local, remote=remote)
    return Repository(PostgresRegistry(registry_dsn), remote)
