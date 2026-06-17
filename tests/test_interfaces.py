"""Structural conformance of the implementations to the ports.

`SnapshotFS`/`checkout` remain stubbed (deferred to a follow-up change), so their
read-only contract is checked here; the blob-plane stores and the in-memory
registry are now implemented and verified behaviorally in their own test modules.
"""

from __future__ import annotations

from datetime import UTC, datetime

import fsspec
import pytest

from sartre import (
    BlobBackend,
    CachingStore,
    CasStore,
    Coordinate,
    FsspecBlobBackend,
    MemoryRegistry,
    Registry,
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


def test_memory_registry_satisfies_registry_protocol() -> None:
    assert isinstance(MemoryRegistry(), Registry)


def test_snapshotfs_is_read_only() -> None:
    store = CasStore(FsspecBlobBackend(fsspec.filesystem("memory"), "b"))
    fs = SnapshotFS(_empty_snapshot(), store)
    with pytest.raises(PermissionError):
        fs._open("a.txt", mode="wb")
    with pytest.raises(PermissionError):
        fs._rm("a.txt")
