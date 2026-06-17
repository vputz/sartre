"""Smoke tests that the stub implementations structurally satisfy the ports.

These verify the interface skeleton is internally consistent: the deferred
implementations expose exactly the methods their Protocols require, and the
read-only / deferred contracts raise as documented. Bodies are not implemented
in this change, so calls raise ``NotImplementedError``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import fsspec
import pytest

from sartre import (
    BlobBackend,
    CachingStore,
    CasStore,
    Coordinate,
    FsspecBlobBackend,
    Registry,
    Repository,
    Snapshot,
    SnapshotFS,
    Store,
)


def _empty_snapshot() -> Snapshot:
    return Snapshot(
        coord=Coordinate("models", "release"),
        version="v1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        metadata={},
        entries=(),
    )


def test_fsspec_backend_satisfies_blobbackend_protocol() -> None:
    backend = FsspecBlobBackend(fsspec.filesystem("memory"), root="blobs")
    assert isinstance(backend, BlobBackend)


def test_cas_and_caching_stores_satisfy_store_protocol() -> None:
    backend = FsspecBlobBackend(fsspec.filesystem("memory"), root="blobs")
    cas = CasStore(backend)
    assert isinstance(cas, Store)
    assert isinstance(CachingStore(local=cas, remote=cas), Store)


def test_snapshotfs_is_read_only() -> None:
    store = CasStore(FsspecBlobBackend(fsspec.filesystem("memory"), "b"))
    fs = SnapshotFS(_empty_snapshot(), store)
    with pytest.raises(PermissionError):
        fs._open("a.txt", mode="wb")
    with pytest.raises(PermissionError):
        fs._rm("a.txt")


def test_repository_read_methods_are_deferred() -> None:
    backend = FsspecBlobBackend(fsspec.filesystem("memory"), root="blobs")
    registry = cast(Registry, _DeferredRegistry())
    repo = Repository(registry=registry, store=CasStore(backend))
    with pytest.raises(NotImplementedError):
        repo.resolve(Coordinate("models", "release"))


class _DeferredRegistry:
    """A throwaway object with the Registry surface, for constructing a Repository."""

    def head(self, *a: object, **k: object) -> str:
        raise NotImplementedError

    def resolve(self, *a: object, **k: object) -> object:
        raise NotImplementedError

    def list_pointers(self, *a: object, **k: object) -> object:
        raise NotImplementedError

    def list_versions(self, *a: object, **k: object) -> object:
        raise NotImplementedError

    def commit(self, *a: object, **k: object) -> str:
        raise NotImplementedError

    def set_pointer(self, *a: object, **k: object) -> None:
        raise NotImplementedError
