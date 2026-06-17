"""Blob-plane implementations: ``CasStore``, ``CachingStore``, ``FsspecBlobBackend``.

Interface stubs only. The signatures, composition, and contracts are fixed here;
the method bodies are deferred to a follow-up change (see the ``add-core-ports``
non-goals). ``CasStore`` centralizes hashing + verify over any dumb
``BlobBackend``; ``CachingStore`` is itself a ``Store`` (the cache is a Store);
``FsspecBlobBackend`` adapts any fsspec filesystem into a ``BlobBackend``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from sartre.hashing import DEFAULT_HASHER, Hasher
from sartre.model import Hash
from sartre.ports import BlobBackend, Store

if TYPE_CHECKING:
    from fsspec import AbstractFileSystem

_DEFERRED = "implementation deferred to a follow-up change (add-core-ports is interface-only)"


class CasStore(Store):
    """Content-addressed :class:`Store` over a dumb :class:`BlobBackend`.

    Centralizes hashing and verification: ``put`` hashes via ``hasher`` and
    writes under the resulting key; reads verify fetched bytes against the
    algorithm named in the key when ``verify`` is set (verify-on-download).
    """

    def __init__(
        self, backend: BlobBackend, hasher: Hasher = DEFAULT_HASHER, *, verify: bool = True
    ) -> None:
        self.backend = backend
        self.hasher = hasher
        self.verify = verify

    def has(self, content_hash: Hash) -> bool:
        raise NotImplementedError(_DEFERRED)

    def open(self, content_hash: Hash) -> BinaryIO:
        raise NotImplementedError(_DEFERRED)

    def get_to(self, content_hash: Hash, dest: Path) -> Path:
        raise NotImplementedError(_DEFERRED)

    def put(self, data: BinaryIO) -> Hash:
        raise NotImplementedError(_DEFERRED)

    def delete(self, content_hash: Hash) -> None:
        raise NotImplementedError(_DEFERRED)


class CachingStore(Store):
    """A :class:`Store` that serves from ``local`` and back-fills from ``remote``.

    Because the cache is keyed by content hash, resolving a new version
    re-downloads only blobs absent from ``local``. Contract: a per-hash lock plus
    temp-file-then-atomic-rename ensures concurrent fetches of the same blob
    neither double-download nor expose a partially written cache file.
    """

    def __init__(self, local: Store, remote: Store) -> None:
        self.local = local
        self.remote = remote

    def has(self, content_hash: Hash) -> bool:
        raise NotImplementedError(_DEFERRED)

    def open(self, content_hash: Hash) -> BinaryIO:
        raise NotImplementedError(_DEFERRED)

    def get_to(self, content_hash: Hash, dest: Path) -> Path:
        raise NotImplementedError(_DEFERRED)

    def put(self, data: BinaryIO) -> Hash:
        raise NotImplementedError(_DEFERRED)

    def delete(self, content_hash: Hash) -> None:
        raise NotImplementedError(_DEFERRED)


class FsspecBlobBackend(BlobBackend):
    """A dumb :class:`BlobBackend` over any fsspec filesystem rooted at a prefix.

    Turns every fsspec-supported target (local, memory, S3, GCS, …) into a usable
    blob backend with no bespoke adapter; wrap it in :class:`CasStore` for full
    content-addressed behavior.
    """

    def __init__(self, fs: AbstractFileSystem, root: str) -> None:
        self.fs = fs
        self.root = root

    def get(self, key: str) -> BinaryIO:
        raise NotImplementedError(_DEFERRED)

    def put(self, key: str, data: BinaryIO) -> None:
        raise NotImplementedError(_DEFERRED)

    def exists(self, key: str) -> bool:
        raise NotImplementedError(_DEFERRED)

    def delete(self, key: str) -> None:
        raise NotImplementedError(_DEFERRED)
