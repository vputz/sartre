"""``Repository`` facade and the ``AsyncRepository`` wrapper.

``Repository`` composes a ``Registry`` and a ``Store`` and implements the read
core (`head`/`resolve`/`open`/`fetch_all`) and the write path (`publish`).
Multi-file fetches parallelize over a thread pool (blob I/O releases the GIL).
``AsyncRepository`` offers awaitable equivalents by offloading to a thread.

`SnapshotFS`/`checkout` are deferred to a follow-up change.
"""

from __future__ import annotations

import asyncio
import io
import tempfile
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sartre.errors import NotFound
from sartre.model import HEAD, Alias, Coordinate, Entry, Head, Ref, Snapshot, Version
from sartre.paths import check_no_case_collisions, normalize_path
from sartre.ports import Registry, Store


def _pointer_ref(pointer: str) -> Ref:
    return Head() if pointer == "head" else Alias(pointer)


class Repository:
    """Composes a :class:`Registry` and a :class:`Store` into the public surface."""

    def __init__(self, registry: Registry, store: Store) -> None:
        self.registry = registry
        self.store = store

    def head(self, coord: Coordinate, ref: Ref = HEAD) -> Version:
        return self.registry.head(coord, ref)

    def resolve(self, coord: Coordinate, ref: Ref = HEAD) -> Snapshot:
        return self.registry.resolve(coord, ref)

    def _entry(self, snap: Snapshot, path: str) -> Entry:
        for entry in snap.entries:
            if entry.path == path:
                return entry
        raise NotFound(f"no entry {path!r} in {snap.coord} @ {snap.version}")

    def _materialize(self, entry: Entry, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if entry.inline is not None:  # small files served from the manifest row
            dest.write_bytes(entry.inline)
            return dest
        return self.store.get_to(entry.content_hash, dest)

    def open(self, snap: Snapshot, path: str) -> Path:
        """Materialize one entry's bytes to a local path, cached by content hash."""
        entry = self._entry(snap, path)
        dest = Path(tempfile.mkdtemp(prefix="sartre-open-")) / Path(path).name
        return self._materialize(entry, dest)

    def fetch_all(self, snap: Snapshot, *, max_workers: int = 8) -> Path:
        """Materialize the whole snapshot to a directory by logical path, in parallel."""
        root = Path(tempfile.mkdtemp(prefix="sartre-fetch-"))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            list(pool.map(lambda e: self._materialize(e, root / e.path), snap.entries))
        return root

    def publish(
        self,
        coord: Coordinate,
        files: Mapping[str, bytes],
        *,
        pointer: str = "head",
        metadata: Mapping[str, object] | None = None,
    ) -> Version:
        """Full-replacement, fail-fast publish: blobs → manifest → CAS pointer."""
        # Canonicalize paths and reject case-collisions up front (write-time path model).
        normalized = {normalize_path(path): data for path, data in files.items()}
        check_no_case_collisions(normalized)

        try:
            start: Version | None = self.registry.head(coord, _pointer_ref(pointer))
        except NotFound:
            start = None  # first publish to this pointer

        entries = tuple(
            Entry(path=path, content_hash=self.store.put(io.BytesIO(data)), size=len(data))
            for path, data in sorted(normalized.items())
        )
        version = self.registry.commit(coord, entries, dict(metadata or {}))
        self.registry.set_pointer(coord, pointer, version, expected=start)  # CAS; raises Conflict
        return version


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
