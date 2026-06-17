"""Tests for manifest_version: the content-hash version identity (define-version-id)."""

from __future__ import annotations

import io

from sartre import Entry, Sha256Hasher, manifest_version


def _entry(path: str, content_hash: str, size: int = 0, inline: bytes | None = None) -> Entry:
    return Entry(path=path, content_hash=content_hash, size=size, inline=inline)


def test_self_describing_prefix() -> None:
    version = manifest_version([_entry("a.txt", "sha256:aa")])
    assert version.startswith("sha256:")


def test_order_independent() -> None:
    a = _entry("a.txt", "sha256:aa")
    b = _entry("b.txt", "sha256:bb")
    assert manifest_version([a, b]) == manifest_version([b, a])


def test_independent_of_size_inline_metadata() -> None:
    # Same (path, content_hash) pairs but differing size/inline must not change identity.
    plain = [_entry("a.txt", "sha256:aa", size=0, inline=None)]
    decorated = [_entry("a.txt", "sha256:aa", size=999, inline=b"the bytes")]
    assert manifest_version(plain) == manifest_version(decorated)


def test_sensitive_to_changed_content_hash() -> None:
    base = [_entry("a.txt", "sha256:aa")]
    changed = [_entry("a.txt", "sha256:zz")]
    assert manifest_version(base) != manifest_version(changed)


def test_sensitive_to_changed_path() -> None:
    base = [_entry("a.txt", "sha256:aa")]
    moved = [_entry("renamed.txt", "sha256:aa")]
    assert manifest_version(base) != manifest_version(moved)


def test_empty_manifest_is_stable() -> None:
    assert manifest_version([]) == manifest_version([])
    assert manifest_version([]).startswith("sha256:")


def test_domain_separated_from_raw_blob_key() -> None:
    # A one-entry manifest's version must not collide with a blob key, even one
    # whose bytes happen to be the manifest's single (path, content_hash) line.
    entry = _entry("a.txt", "sha256:aa")
    version = manifest_version([entry])
    raw_blob_key = Sha256Hasher().hash(io.BytesIO(b"a.txt\x00sha256:aa"))
    assert version != raw_blob_key
