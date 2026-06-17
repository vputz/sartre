"""``Repository`` facade and the ``AsyncRepository`` wrapper.

Interface stubs. ``Repository`` composes a ``Registry`` and a ``Store`` and
exposes the read surface plus ``publish``; multi-file operations parallelize over
a thread pool (blob I/O releases the GIL — no async core needed).
``AsyncRepository`` offers awaitable equivalents by offloading the sync core to a
thread, never duplicating its logic. Method bodies are deferred to a follow-up
change.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from sartre.model import HEAD, Coordinate, Entry, Ref, Snapshot, Version
from sartre.ports import Registry, Store

_DEFERRED = "implementation deferred to a follow-up change (add-core-ports is interface-only)"


class Repository:
    """Composes a :class:`Registry` and a :class:`Store` into the public surface."""

    def __init__(self, registry: Registry, store: Store) -> None:
        self.registry = registry
        self.store = store

    def head(self, coord: Coordinate, ref: Ref = HEAD) -> Version:
        raise NotImplementedError(_DEFERRED)

    def resolve(self, coord: Coordinate, ref: Ref = HEAD) -> Snapshot:
        raise NotImplementedError(_DEFERRED)

    def open(self, snap: Snapshot, path: str) -> Path:
        """Materialize one entry's bytes to a local path, cached by content hash."""
        raise NotImplementedError(_DEFERRED)

    def fetch_all(self, snap: Snapshot, *, max_workers: int = 8) -> Path:
        """Materialize the whole snapshot, fetching blobs in parallel."""
        raise NotImplementedError(_DEFERRED)

    def checkout(
        self, snap: Snapshot, dest: Path, *, max_workers: int = 8, link: str = "copy"
    ) -> Path:
        """Lay out the snapshot as a directory tree by logical path under ``dest``."""
        raise NotImplementedError(_DEFERRED)

    def publish(
        self,
        coord: Coordinate,
        entries: Iterable[Entry],
        *,
        pointer: str = "head",
        metadata: Mapping[str, Any] | None = None,
    ) -> Version:
        """Upload missing blobs, commit the manifest, then CAS-advance the pointer."""
        raise NotImplementedError(_DEFERRED)


class AsyncRepository:
    """Awaitable wrapper that offloads the synchronous :class:`Repository` to a thread."""

    def __init__(self, repository: Repository) -> None:
        self._sync = repository

    async def head(self, coord: Coordinate, ref: Ref = HEAD) -> Version:
        return await asyncio.to_thread(self._sync.head, coord, ref)

    async def resolve(self, coord: Coordinate, ref: Ref = HEAD) -> Snapshot:
        return await asyncio.to_thread(self._sync.resolve, coord, ref)

    async def open(self, snap: Snapshot, path: str) -> Path:
        return await asyncio.to_thread(self._sync.open, snap, path)

    async def fetch_all(self, snap: Snapshot, *, max_workers: int = 8) -> Path:
        return await asyncio.to_thread(self._sync.fetch_all, snap, max_workers=max_workers)
