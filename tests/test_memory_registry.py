"""Behavioral tests for MemoryRegistry: head, resolve, commit, CAS, ordering."""

from __future__ import annotations

import pytest

from sartre import (
    Alias,
    Conflict,
    Coordinate,
    Entry,
    MemoryRegistry,
    NotFound,
    Pin,
)

COORD = Coordinate("models", "release")


def _entries(tag: str) -> tuple[Entry, ...]:
    return (Entry(f"{tag}.bin", f"sha256:{tag * 4}", 3),)


def test_resolve_unknown_raises_not_found() -> None:
    reg = MemoryRegistry()
    with pytest.raises(NotFound):
        reg.resolve(COORD)
    with pytest.raises(NotFound):
        reg.head(COORD)


def test_commit_is_content_idempotent() -> None:
    reg = MemoryRegistry()
    v1 = reg.commit(COORD, _entries("a"), {})
    v2 = reg.commit(COORD, _entries("a"), {"note": "different metadata"})
    assert v1 == v2  # identity is the entries, not metadata


def test_commit_does_not_move_pointer() -> None:
    reg = MemoryRegistry()
    reg.commit(COORD, _entries("a"), {})
    with pytest.raises(NotFound):  # head still unset after a bare commit
        reg.head(COORD)


def test_head_and_resolve_after_set_pointer() -> None:
    reg = MemoryRegistry()
    version = reg.commit(COORD, _entries("a"), {"k": 1})
    reg.set_pointer(COORD, "head", version, expected=None)
    assert reg.head(COORD) == version
    snap = reg.resolve(COORD)
    assert snap.version == version
    assert snap.metadata == {"k": 1}
    assert snap.entries == _entries("a")


def test_cas_conflict_leaves_pointer_unchanged() -> None:
    reg = MemoryRegistry()
    v1 = reg.commit(COORD, _entries("a"), {})
    v2 = reg.commit(COORD, _entries("b"), {})
    reg.set_pointer(COORD, "head", v1, expected=None)
    with pytest.raises(Conflict):
        reg.set_pointer(COORD, "head", v2, expected=None)  # stale expected
    assert reg.head(COORD) == v1


def test_set_pointer_rejects_uncommitted_version() -> None:
    reg = MemoryRegistry()
    with pytest.raises(NotFound):
        reg.set_pointer(COORD, "head", "sha256:" + "0" * 64, expected=None)


def test_list_versions_in_commit_order() -> None:
    reg = MemoryRegistry()
    versions = []
    prev = None
    for tag in ("a", "b", "c"):
        v = reg.commit(COORD, _entries(tag), {})
        reg.set_pointer(COORD, "head", v, expected=prev)
        versions.append(v)
        prev = v
    assert list(reg.list_versions(COORD)) == versions


def test_alias_pointer_and_pin() -> None:
    reg = MemoryRegistry()
    version = reg.commit(COORD, _entries("a"), {})
    reg.set_pointer(COORD, "production", version, expected=None)
    assert reg.head(COORD, Alias("production")) == version
    assert reg.resolve(COORD, Pin(version)).version == version


def test_promotion_shares_manifest_across_coords() -> None:
    reg = MemoryRegistry()
    dev, release = Coordinate("models", "dev"), Coordinate("models", "release")
    version = reg.commit(dev, _entries("a"), {})
    reg.set_pointer(dev, "head", version, expected=None)
    # Promote: point release's head at the same version — no re-commit.
    reg.set_pointer(release, "head", version, expected=None)
    assert reg.head(release) == version
    assert reg.resolve(release).entries == reg.resolve(dev).entries
