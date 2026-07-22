"""Repository-level tests for the lease self-check and the grace-period backstop.

The self-check tests drive a publish whose lease lapses mid-flight (via a
``MemoryRegistry`` subclass that scripts ``renew_lease`` results) and assert the
publish aborts rather than commit/point over blobs GC may reclaim — the safety
property `GCLease.tla` proved. The grace tests drive `gc` on an injected clock over
a settable-mtime backend, exercising the `GCGrace.tla` boundary in code.
"""

from __future__ import annotations

import io
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import BinaryIO

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sartre import (
    CasStore,
    Conflict,
    Coordinate,
    LeaseExpired,
    MemoryRegistry,
    NotFound,
    Repository,
    RetentionPolicy,
)
from sartre.model import Hash
from sartre.ports import DEFAULT_LEASE_TTL, BlobBackend, LeaseId

COORD = Coordinate("models", "release")
_names = st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=3)
_filesets = st.dictionaries(_names, st.binary(max_size=8), min_size=1, max_size=3)


class _ScriptedRenewRegistry(MemoryRegistry):
    """A registry whose ``renew_lease`` returns scripted results, to force a lease to
    lapse at a chosen publish self-check. A scripted ``False`` also releases the lease,
    emulating a real expiry so a concurrent GC sees the blobs unprotected."""

    def __init__(self, renew_results: Iterable[bool]) -> None:
        super().__init__()
        self._scripted = list(renew_results)

    def renew_lease(self, lease_id: LeaseId, ttl: float = DEFAULT_LEASE_TTL) -> bool:
        if self._scripted:
            ok = self._scripted.pop(0)
            if not ok:
                super().release_lease(lease_id)  # a lapsed lease is gone for GC too
            return ok
        return super().renew_lease(lease_id, ttl)


def _repo(registry: MemoryRegistry) -> Repository:
    store = CasStore(_MtimeBackend())
    # A large heartbeat interval keeps the background renew from firing during the test,
    # so only the two publish self-checks consume the scripted renew results.
    return Repository(registry, store, heartbeat_interval=3600.0)


def test_publish_aborts_when_lease_lapses_before_commit() -> None:
    reg = _ScriptedRenewRegistry([False])  # pre-commit self-check fails
    repo = _repo(reg)
    with pytest.raises(LeaseExpired):
        repo.publish(COORD, {"w.bin": b"WEIGHTS"})
    with pytest.raises(NotFound):
        repo.resolve(COORD)  # nothing was committed or pointed


def test_publish_aborts_when_lease_lapses_before_advance() -> None:
    reg = _ScriptedRenewRegistry([True, False])  # pass pre-commit, fail pre-advance
    repo = _repo(reg)
    with pytest.raises(LeaseExpired):
        repo.publish(COORD, {"w.bin": b"WEIGHTS"})
    with pytest.raises(NotFound):
        repo.resolve(COORD)  # committed-but-unpointed: the pointer never advanced


def test_lease_expired_is_a_retryable_conflict() -> None:
    assert issubclass(LeaseExpired, Conflict)


@settings(max_examples=40, deadline=None)
@given(files=_filesets, lapse=st.sampled_from(["commit", "advance", "never"]))
def test_self_check_never_leaves_a_dangling_pointer(files: dict[str, bytes], lapse: str) -> None:
    scripted = {"commit": [False], "advance": [True, False], "never": []}[lapse]
    reg = _ScriptedRenewRegistry(scripted)
    repo = _repo(reg)
    try:
        repo.publish(COORD, files)
    except LeaseExpired:
        pass  # a lapsed publish aborts; that is the point
    repo.gc(RetentionPolicy())  # sweep whatever the aborted/committed publish left
    # Invariant: the pointer either does not resolve, or resolves with all blobs present.
    try:
        snap = repo.resolve(COORD)
    except NotFound:
        return
    for entry in snap.entries:
        assert repo.store.has(entry.content_hash)  # never a committed pointer over a swept blob


# --- grace-period backstop ---


class _MtimeBackend(BlobBackend):
    """An in-memory blob backend with a settable clock, so a blob's recorded mtime is
    the value of ``now`` when it was promoted. Drives the grace boundary deterministically.
    """

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}
        self._mtime: dict[str, float] = {}
        self._staging: dict[str, bytes] = {}
        self._n = 0
        self.now = 0.0

    def get(self, key: str) -> BinaryIO:
        return io.BytesIO(self._data[key])

    def stage(self, data: BinaryIO) -> str:
        self._n += 1
        staging_key = f".tmp/{self._n}"
        self._staging[staging_key] = data.read()
        return staging_key

    def promote(self, staging_key: str, final_key: str) -> None:
        payload = self._staging.pop(staging_key)
        if final_key not in self._data:  # idempotent: keep existing content + mtime
            self._data[final_key] = payload
            self._mtime[final_key] = self.now

    def exists(self, key: str) -> bool:
        return key in self._data

    def delete(self, key: str) -> None:
        self._data.pop(key, None)
        self._mtime.pop(key, None)
        self._staging.pop(key, None)

    def list(self) -> Iterable[str]:
        return list(self._data)

    def mtime(self, key: str) -> float | None:
        return self._mtime.get(key)


def _orphan_repo() -> tuple[Repository, CasStore, _MtimeBackend]:
    backend = _MtimeBackend()
    store = CasStore(backend)
    return Repository(MemoryRegistry(), store, heartbeat_interval=3600.0), store, backend


def test_grace_retains_young_blob_and_reclaims_aged_one() -> None:
    repo, store, backend = _orphan_repo()
    base = 1_000_000.0
    backend.now = base
    key: Hash = store.put(io.BytesIO(b"orphan-upload"))  # an unleased, unreferenced blob

    grace = RetentionPolicy(grace=timedelta(seconds=10))
    young = repo.gc(grace, clock=lambda: datetime.fromtimestamp(base + 5, UTC))
    assert key not in young.deleted_blobs and store.has(key)  # within grace → retained

    aged = repo.gc(grace, clock=lambda: datetime.fromtimestamp(base + 20, UTC))
    assert key in aged.deleted_blobs and not store.has(key)  # past grace → reclaimed


def test_grace_defaults_off_reclaims_immediately() -> None:
    repo, store, backend = _orphan_repo()
    backend.now = 1_000_000.0
    key: Hash = store.put(io.BytesIO(b"orphan-upload"))
    # No grace (default): an unreferenced, unleased blob is reclaimed regardless of age.
    result = repo.gc(RetentionPolicy(), clock=lambda: datetime.fromtimestamp(1_000_001.0, UTC))
    assert key in result.deleted_blobs and not store.has(key)
