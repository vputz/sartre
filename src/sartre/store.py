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

import hashlib
import shutil
import threading
import uuid
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, cast

from sartre.errors import IntegrityError, NotFound
from sartre.hashing import DEFAULT_HASHER, Hasher, algorithm_of, hasher_for, make_key
from sartre.model import Hash
from sartre.ports import BlobBackend, Store


class _HashingReader:
    """A read-through tee: wraps a readable stream and hashes bytes as they are read.

    The backend's copy loop pulls bytes through this wrapper, so the content hash falls
    out of the same single streaming pass that writes staging (put) or fills a
    destination (verified read) — no whole-blob buffer. After the inner stream is
    exhausted, :meth:`key` is the content hash and :meth:`length` the byte count.
    """

    def __init__(self, inner: BinaryIO, hasher: Hasher) -> None:
        self._inner = inner
        self._algorithm = hasher.algorithm
        self._digest = hashlib.new(hasher.algorithm)
        self._length = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._inner.read(size)
        self._digest.update(chunk)
        self._length += len(chunk)
        return chunk

    def key(self) -> Hash:
        return cast(Hash, make_key(self._algorithm, self._digest.hexdigest()))

    def length(self) -> int:
        return self._length

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

    Writes are crash-safe and streaming: ``stage`` streams bytes into a reserved
    ``{root}/.tmp/`` namespace, and ``promote`` atomically renames the staged object
    onto its final key, so a blob appears at its key only once fully written. ``list``
    excludes that namespace; ``sweep_temp`` reclaims staging objects orphaned by a
    crashed write.
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

    def _staging_key(self) -> str:
        return f"{self._TEMP_DIR}/{uuid.uuid4().hex}"  # a key under the .tmp namespace

    def get(self, key: str) -> BinaryIO:
        if not self.exists(key):
            raise NotFound(f"blob not found: {key}")
        # fsspec types open() loosely; "rb"/"wb" are binary at runtime.
        return cast(BinaryIO, self.fs.open(self._path(key), "rb"))

    def stage(self, data: BinaryIO) -> str:
        """Stream ``data`` to a fresh staging key and return it. Bounded memory."""
        self.fs.makedirs(self._temp_root, exist_ok=True)
        staging_key = self._staging_key()
        with cast(BinaryIO, self.fs.open(self._path(staging_key), "wb")) as handle:
            shutil.copyfileobj(data, handle)  # chunked copy; never buffers the whole blob
        return staging_key

    def promote(self, staging_key: str, final_key: str) -> None:
        """Atomically make the staged bytes appear at ``final_key``; idempotent.

        If ``final_key`` already exists (identical content, e.g. a concurrent writer
        won the race), the staged object is discarded rather than overwritten.
        """
        if self.exists(final_key):
            self.fs.rm(self._path(staging_key))
        else:
            self.fs.mv(self._path(staging_key), self._path(final_key))

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
        """Stream ``data`` to the backend and return its content hash. Bounded memory.

        The hash is unknown until the bytes are read, so a hashing tee rides the single
        streaming pass into staging; ``promote`` then names the blob by its hash (and
        discards the staging object when identical content is already present).
        """
        reader = _HashingReader(data, self.hasher)
        staging_key = self.backend.stage(cast(BinaryIO, reader))  # only .read() is used
        key = reader.key()
        self.backend.promote(staging_key, key)
        return key

    def open(self, content_hash: Hash) -> BinaryIO:
        """Return a seekable handle for random access — NOT verified per read.

        A partial/seek read cannot be checked against a whole-blob content hash, so this
        serves the backend's native handle directly (range reads on a remote backend).
        Callers needing verified bytes use :meth:`get_to` (verified whole-blob) or a
        :class:`CachingStore` (verified on local materialization).
        """
        return self.backend.get(content_hash)

    def get_to(self, content_hash: Hash, dest: Path) -> Path:
        """Materialize the whole blob to ``dest``, verifying integrity as it streams.

        Hashes while copying and, at end-of-stream, deletes ``dest`` and raises
        :class:`IntegrityError` if the bytes do not hash to ``content_hash``.
        """
        verifier = hasher_for(algorithm_of(content_hash))
        reader = _HashingReader(self.backend.get(content_hash), verifier)
        with dest.open("wb") as out:
            shutil.copyfileobj(reader, out)
        if self.verify and reader.key() != content_hash:
            dest.unlink(missing_ok=True)
            raise IntegrityError(
                f"blob {content_hash} failed verification (got {reader.key()})"
            )
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
        """Materialize the blob into ``local``, verifying it on the way in.

        Streams the (unverified) remote handle into ``local.put``, which re-hashes; if
        the result does not match the requested hash the misfiled blob is removed and an
        integrity error is raised. So a subsequent seek is served from a verified local
        copy — this is what makes a `CachingStore` the verified random-access path.
        """
        with self._lock_for(content_hash):
            if not self.local.has(content_hash):
                got = self.local.put(self.remote.open(content_hash))
                if got != content_hash:
                    self.local.delete(got)
                    raise IntegrityError(
                        f"blob {content_hash} failed verification (got {got})"
                    )

    def has(self, content_hash: Hash) -> bool:
        return self.local.has(content_hash) or self.remote.has(content_hash)

    def open(self, content_hash: Hash) -> BinaryIO:
        self._ensure_local(content_hash)
        return self.local.open(content_hash)

    def get_to(self, content_hash: Hash, dest: Path) -> Path:
        self._ensure_local(content_hash)
        return self.local.get_to(content_hash, dest)

    def put(self, data: BinaryIO) -> Hash:
        # Stream to remote (the source of truth) in bounded memory; the local cache is
        # populated lazily on first read (see _ensure_local), so a large publish does not
        # re-download its own blob just to warm the cache.
        return self.remote.put(data)

    def delete(self, content_hash: Hash) -> None:
        if self.local.has(content_hash):
            self.local.delete(content_hash)
        if self.remote.has(content_hash):
            self.remote.delete(content_hash)

    def list(self) -> Iterable[Hash]:
        return self.remote.list()  # remote is the source of truth; local is a subset

    def mtime(self, content_hash: Hash) -> float | None:
        return self.remote.mtime(content_hash)  # remote is authoritative for age
