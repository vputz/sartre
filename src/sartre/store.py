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
import uuid
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, cast

from sartre.errors import IntegrityError, NotFound
from sartre.hashing import DEFAULT_HASHER, Hasher, algorithm_of, hasher_for
from sartre.model import Hash
from sartre.ports import BlobBackend, Store

if TYPE_CHECKING:
    from fsspec import AbstractFileSystem


def _as_epoch(value: Any) -> float | None:
    """Coerce an fsspec mtime field (datetime, epoch number, or ISO string) to epoch."""
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


class FsspecBlobBackend(BlobBackend):
    """A dumb :class:`BlobBackend` over any fsspec filesystem rooted at a prefix.

    ``put`` is crash-safe: bytes are staged under a reserved ``{root}/.tmp/``
    namespace and renamed onto the final key, so a blob appears at its key only
    once fully written. ``list`` excludes that namespace; ``sweep_temp`` reclaims
    staging objects orphaned by a crashed ``put``.
    """

    _TEMP_DIR = ".tmp"

    def __init__(self, fs: AbstractFileSystem, root: str) -> None:
        self.fs = fs
        self.root = root.rstrip("/")

    def _path(self, key: str) -> str:
        return f"{self.root}/{key}"

    @property
    def _temp_root(self) -> str:
        return f"{self.root}/{self._TEMP_DIR}"

    def _staging_path(self) -> str:
        return f"{self._temp_root}/{uuid.uuid4().hex}"

    def get(self, key: str) -> BinaryIO:
        if not self.exists(key):
            raise NotFound(f"blob not found: {key}")
        # fsspec types open() loosely; "rb"/"wb" are binary at runtime.
        return cast(BinaryIO, self.fs.open(self._path(key), "rb"))

    def put(self, key: str, data: BinaryIO) -> None:
        if self.exists(key):  # content-addressed: identical bytes already stored
            return
        self.fs.makedirs(self._temp_root, exist_ok=True)
        staging = self._staging_path()
        try:
            with cast(BinaryIO, self.fs.open(staging, "wb")) as handle:
                shutil.copyfileobj(data, handle)
            # Publish atomically: the blob appears at its key only now, whole.
            self.fs.mv(staging, self._path(key))
        finally:
            if self.fs.exists(staging):  # best-effort cleanup on failure
                self.fs.rm(staging)

    def exists(self, key: str) -> bool:
        return bool(self.fs.exists(self._path(key)))

    def delete(self, key: str) -> None:
        self.fs.rm(self._path(key))

    def list(self) -> Iterable[str]:
        if not self.fs.exists(self.root):
            return
        for path in self.fs.ls(self.root, detail=False):
            name = str(path).rsplit("/", 1)[-1]  # basename == key (hashes carry no '/')
            if not name.startswith("."):  # skip the reserved .tmp namespace
                yield name

    def sweep_temp(self) -> int:
        """Delete staging objects orphaned by crashed puts; return the count reclaimed."""
        if not self.fs.exists(self._temp_root):
            return 0
        staged = list(self.fs.ls(self._temp_root, detail=False))
        for path in staged:
            self.fs.rm(path)
        return len(staged)

    def mtime(self, key: str) -> float | None:
        if not self.exists(key):
            return None
        info = self.fs.info(self._path(key))
        # fsspec backends spell mtime differently; take the first recognised field.
        for field_name in ("mtime", "LastModified", "last_modified", "created", "ctime"):
            if field_name in info:
                return _as_epoch(info[field_name])
        return None


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

    def list(self) -> Iterable[Hash]:
        return self.backend.list()

    def mtime(self, content_hash: Hash) -> float | None:
        return self.backend.mtime(content_hash)


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

    def list(self) -> Iterable[Hash]:
        return self.remote.list()  # remote is the source of truth; local is a subset

    def mtime(self, content_hash: Hash) -> float | None:
        return self.remote.mtime(content_hash)  # remote is authoritative for age
