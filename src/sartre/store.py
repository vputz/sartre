"""Blob-plane implementations: ``CasStore``, ``CachingStore``, ``FsspecBlobBackend``.

``FsspecBlobBackend`` adapts any fsspec filesystem into a dumb ``BlobBackend``;
``CasStore`` adds content-addressing and verify-on-read over any backend;
``CachingStore`` is itself a ``Store`` (the cache is a Store) and serves from a
local store, back-filling from a remote one with a per-hash lock.

This reference implementation reads blobs fully into memory to hash/verify them —
fine for the in-memory backend; streaming-to-temp for huge blobs over a real
backend is a later optimization.
"""

from __future__ import annotations

import io
import shutil
import threading
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, cast

from sartre.errors import IntegrityError, NotFound
from sartre.hashing import DEFAULT_HASHER, Hasher, algorithm_of, hasher_for
from sartre.model import Hash
from sartre.ports import BlobBackend, Store

if TYPE_CHECKING:
    from fsspec import AbstractFileSystem


class FsspecBlobBackend(BlobBackend):
    """A dumb :class:`BlobBackend` over any fsspec filesystem rooted at a prefix."""

    def __init__(self, fs: AbstractFileSystem, root: str) -> None:
        self.fs = fs
        self.root = root.rstrip("/")

    def _path(self, key: str) -> str:
        return f"{self.root}/{key}"

    def get(self, key: str) -> BinaryIO:
        if not self.exists(key):
            raise NotFound(f"blob not found: {key}")
        # fsspec types open() loosely; "rb"/"wb" are binary at runtime.
        return cast(BinaryIO, self.fs.open(self._path(key), "rb"))

    def put(self, key: str, data: BinaryIO) -> None:
        with cast(BinaryIO, self.fs.open(self._path(key), "wb")) as handle:
            shutil.copyfileobj(data, handle)

    def exists(self, key: str) -> bool:
        return bool(self.fs.exists(self._path(key)))

    def delete(self, key: str) -> None:
        self.fs.rm(self._path(key))


class CasStore(Store):
    """Content-addressed :class:`Store` over a dumb :class:`BlobBackend`."""

    def __init__(
        self, backend: BlobBackend, hasher: Hasher = DEFAULT_HASHER, *, verify: bool = True
    ) -> None:
        self.backend = backend
        self.hasher = hasher
        self.verify = verify

    def has(self, content_hash: Hash) -> bool:
        return self.backend.exists(content_hash)

    def put(self, data: BinaryIO) -> Hash:
        payload = data.read()
        key = self.hasher.hash(io.BytesIO(payload))
        if not self.backend.exists(key):  # idempotent: identical bytes are a no-op
            self.backend.put(key, io.BytesIO(payload))
        return key

    def _load(self, content_hash: Hash) -> bytes:
        payload = self.backend.get(content_hash).read()
        if self.verify:
            actual = hasher_for(algorithm_of(content_hash)).hash(io.BytesIO(payload))
            if actual != content_hash:
                raise IntegrityError(
                    f"blob {content_hash} failed verification (got {actual})"
                )
        return payload

    def open(self, content_hash: Hash) -> BinaryIO:
        return io.BytesIO(self._load(content_hash))

    def get_to(self, content_hash: Hash, dest: Path) -> Path:
        dest.write_bytes(self._load(content_hash))
        return dest

    def delete(self, content_hash: Hash) -> None:
        self.backend.delete(content_hash)


class CachingStore(Store):
    """A :class:`Store` serving from ``local`` and back-filling from ``remote``.

    Keyed by content hash, so resolving a new version re-downloads only blobs
    absent from ``local``. A per-hash lock plus the underlying atomic writes mean
    concurrent fetches of the same blob download at most once.
    """

    def __init__(self, local: Store, remote: Store) -> None:
        self.local = local
        self.remote = remote
        self._locks: dict[Hash, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, content_hash: Hash) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(content_hash, threading.Lock())

    def _ensure_local(self, content_hash: Hash) -> None:
        with self._lock_for(content_hash):
            if not self.local.has(content_hash):
                self.local.put(self.remote.open(content_hash))

    def has(self, content_hash: Hash) -> bool:
        return self.local.has(content_hash) or self.remote.has(content_hash)

    def open(self, content_hash: Hash) -> BinaryIO:
        self._ensure_local(content_hash)
        return self.local.open(content_hash)

    def get_to(self, content_hash: Hash, dest: Path) -> Path:
        self._ensure_local(content_hash)
        return self.local.get_to(content_hash, dest)

    def put(self, data: BinaryIO) -> Hash:
        payload = data.read()
        key = self.remote.put(io.BytesIO(payload))  # remote is the source of truth
        self.local.put(io.BytesIO(payload))  # populate the cache write-through
        return key

    def delete(self, content_hash: Hash) -> None:
        if self.local.has(content_hash):
            self.local.delete(content_hash)
        if self.remote.has(content_hash):
            self.remote.delete(content_hash)
