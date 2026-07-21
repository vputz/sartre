"""Behavioral + property tests for garbage collection and retention.

The stateful machine mirrors the publish/GC TLA+ model
(`openspec/specs/garbage-collection/model/GC.tla`), asserting `BlobSafe` on the
live backend: every reachable tip resolves to fully present blobs after every step.
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime, timedelta

import fsspec
import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from sartre import (
    Alias,
    CachingStore,
    CasStore,
    Conflict,
    Coordinate,
    Entry,
    FsspecBlobBackend,
    MemoryRegistry,
    Pin,
    Repository,
    RetentionPolicy,
    manifest_version,
)

COORD = Coordinate("models", "release")


def _cas() -> CasStore:
    fs = fsspec.filesystem("memory")
    return CasStore(FsspecBlobBackend(fs, root=f"b-{uuid.uuid4().hex}"))


def _repo() -> Repository:
    return Repository(MemoryRegistry(), CachingStore(local=_cas(), remote=_cas()))


def _hashes(repo: Repository, version: str) -> set[str]:
    return {e.content_hash for e in repo.resolve(COORD, Pin(version)).entries}


def test_unreferenced_blob_reclaimed_shared_blob_retained() -> None:
    repo = _repo()
    v1 = repo.publish(COORD, {"a.txt": b"one", "shared.txt": b"S"})
    v2 = repo.publish(COORD, {"a.txt": b"two", "shared.txt": b"S"})  # shared bytes dedup
    only_v1 = _hashes(repo, v1) - _hashes(repo, v2)  # hash of b"one"
    shared = _hashes(repo, v1) & _hashes(repo, v2)  # hash of b"S"

    result = repo.gc()  # keep_last_n=0: only the tip (v2) is retained
    assert result.dropped_versions == (v1,)
    stored = set(repo.store.list())
    assert only_v1.isdisjoint(stored)  # v1's unique blob reclaimed
    assert shared <= stored  # shared blob kept (v2 still references it)
    assert repo.resolve(COORD).version == v2  # tip intact


def test_pointer_and_tag_targets_are_protected() -> None:
    repo = _repo()
    v1 = repo.publish(COORD, {"a.txt": b"one"})
    repo.registry.set_pointer(COORD, "prod", v1, expected=None)  # tag prod -> v1
    repo.publish(COORD, {"a.txt": b"two"})  # head -> v2, prod still v1

    result = repo.gc()  # v1 protected by the prod tag despite keep_last_n=0
    assert v1 not in result.dropped_versions
    assert _hashes(repo, v1) <= set(repo.store.list())
    assert repo.resolve(COORD, Pin(v1)).version == v1
    assert repo.head(COORD, Alias("prod")) == v1


def test_keep_last_n_retains_newest() -> None:
    repo = _repo()
    v1 = repo.publish(COORD, {"a.txt": b"1"})
    v2 = repo.publish(COORD, {"a.txt": b"2"})
    v3 = repo.publish(COORD, {"a.txt": b"3"})

    result = repo.gc(RetentionPolicy(keep_last_n=2))  # keep v2, v3 (+ tip v3)
    assert result.dropped_versions == (v1,)
    assert repo.resolve(COORD, Pin(v2)).version == v2
    assert repo.resolve(COORD, Pin(v3)).version == v3


def test_keep_within_uses_injected_clock() -> None:
    repo = _repo()
    v1 = repo.publish(COORD, {"a.txt": b"1"})
    repo.publish(COORD, {"a.txt": b"2"})  # tip = v2
    now = datetime.now(UTC)

    # Within the window (clock = now): v1 is recent -> retained.
    kept = repo.gc(RetentionPolicy(keep_within=timedelta(hours=1)), clock=lambda: now)
    assert v1 not in kept.dropped_versions
    assert repo.resolve(COORD, Pin(v1)).version == v1

    # Advance the clock 2h: v1 falls outside the 1h window and is not the tip -> dropped.
    future = now + timedelta(hours=2)
    aged = repo.gc(RetentionPolicy(keep_within=timedelta(hours=1)), clock=lambda: future)
    assert aged.dropped_versions == (v1,)


def test_gc_is_idempotent() -> None:
    repo = _repo()
    repo.publish(COORD, {"a.txt": b"1"})
    repo.publish(COORD, {"a.txt": b"2"})
    first = repo.gc()
    assert first.dropped_versions or first.deleted_blobs  # did something
    second = repo.gc()  # no intervening writes
    assert second.dropped_versions == ()
    assert second.deleted_blobs == ()


def test_lease_protects_in_flight_blob_then_released_is_collectable() -> None:
    repo = _repo()
    data = b"inflight"
    h = repo.store.put(io.BytesIO(data))  # uploaded, not yet committed
    version = manifest_version((Entry("f", h, len(data)),))
    lease = repo.registry.acquire_lease(version, {h})

    repo.gc()  # blob is leased -> a root -> must survive
    assert repo.store.has(h)

    repo.registry.release_lease(lease)
    repo.gc()  # now orphaned (no manifest, no lease) -> collectable
    assert not repo.store.has(h)


def test_drop_version_refuses_pointer_target() -> None:
    repo = _repo()
    v1 = repo.publish(COORD, {"a.txt": b"1"})
    with pytest.raises(Conflict):
        repo.registry.drop_version(v1)  # v1 is the current head tip


# --- stateful machine mirroring GC.tla's BlobSafe ---

_names = st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=3)
_filesets = st.dictionaries(_names, st.binary(max_size=16), min_size=1, max_size=3)
_coord_index = st.integers(min_value=0, max_value=1)
_keep_n = st.integers(min_value=0, max_value=2)


class GCMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.repo = Repository(MemoryRegistry(), CachingStore(local=_cas(), remote=_cas()))
        self.coords = [Coordinate("m", "dev"), Coordinate("m", "release")]
        self.tips: dict[tuple[str, str], str] = {}
        self.content: dict[str, dict[str, bytes]] = {}

    def _key(self, coord: Coordinate) -> tuple[str, str]:
        return (coord.name, coord.env)

    @rule(ci=_coord_index, files=_filesets)
    def publish(self, ci: int, files: dict[str, bytes]) -> None:
        coord = self.coords[ci]
        version = self.repo.publish(coord, files)
        self.tips[self._key(coord)] = version
        self.content[version] = dict(files)

    @rule(ci=_coord_index, files=_filesets)
    def orphan(self, ci: int, files: dict[str, bytes]) -> None:
        # A crashed publish: blobs stored + manifest committed, but no pointer and
        # no lease. Its bytes may be reclaimed; it is never a tracked tip.
        coord = self.coords[ci]
        entries = tuple(
            Entry(p, self.repo.store.put(io.BytesIO(v)), len(v)) for p, v in files.items()
        )
        self.repo.registry.commit(coord, entries, {})

    @rule(keep_n=_keep_n)
    def collect(self, keep_n: int) -> None:
        self.repo.gc(RetentionPolicy(keep_last_n=keep_n))

    @invariant()
    def tips_resolve_to_present_blobs(self) -> None:
        for (name, env), version in self.tips.items():
            coord = Coordinate(name, env)
            assert self.repo.head(coord) == version
            snap = self.repo.resolve(coord)
            got = {e.path: self.repo.open(snap, e.path).read_bytes() for e in snap.entries}
            assert got == self.content[version]


GCMachine.TestCase.settings = settings(max_examples=40, stateful_step_count=24, deadline=None)
TestGCMachine = GCMachine.TestCase
