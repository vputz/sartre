"""Property-based tests (Hypothesis) for the reference backend's algebra."""

from __future__ import annotations

import uuid

import fsspec
from hypothesis import given
from hypothesis import strategies as st

from sartre import (
    CachingStore,
    CasStore,
    Coordinate,
    Entry,
    FsspecBlobBackend,
    MemoryRegistry,
    Repository,
    manifest_version,
)

# Single-segment, lowercase, non-empty names: already canonical and free of
# nesting/case collisions, so publish round-trips without path rejection.
_names = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=6)
_filesets = st.dictionaries(_names, st.binary(max_size=64), min_size=1, max_size=5)
_entries = st.builds(
    Entry,
    path=_names,
    content_hash=st.text(min_size=1, max_size=8).map(lambda s: f"sha256:{s}"),
    size=st.integers(min_value=0, max_value=10_000),
)


def _cas() -> CasStore:
    fs = fsspec.filesystem("memory")
    return CasStore(FsspecBlobBackend(fs, root=f"b-{uuid.uuid4().hex}"))


def _repo() -> Repository:
    return Repository(MemoryRegistry(), CachingStore(local=_cas(), remote=_cas()))


@given(files=_filesets)
def test_publish_resolve_roundtrip(files: dict[str, bytes]) -> None:
    repo = _repo()
    coord = Coordinate("m", "release")
    version = repo.publish(coord, files)
    snap = repo.resolve(coord)
    assert snap.version == version
    assert {e.path for e in snap.entries} == set(files)
    for entry in snap.entries:
        assert repo.open(snap, entry.path).read_bytes() == files[entry.path]


@given(files=_filesets)
def test_publish_is_idempotent(files: dict[str, bytes]) -> None:
    repo = _repo()
    coord = Coordinate("m", "release")
    assert repo.publish(coord, files) == repo.publish(coord, files)


@given(
    data=st.data(),
    entries=st.lists(_entries, min_size=1, max_size=6, unique_by=lambda e: e.path),
)
def test_manifest_version_is_permutation_invariant(
    data: st.DataObject, entries: list[Entry]
) -> None:
    shuffled = data.draw(st.permutations(entries))
    assert manifest_version(entries) == manifest_version(shuffled)
