"""Tests for self-describing content-hash keys and the default hasher."""

from __future__ import annotations

import io

import pytest

from sartre.hashing import Sha256Hasher, algorithm_of, make_key, parse_key

# sha256 of the empty byte string.
_EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_make_and_parse_key_round_trip() -> None:
    key = make_key("sha256", "abc123")
    assert key == "sha256:abc123"
    assert parse_key(key) == ("sha256", "abc123")
    assert algorithm_of(key) == "sha256"


def test_parse_key_rejects_malformed() -> None:
    for bad in ["", "nocolon", ":missingalgo", "missingdigest:"]:
        with pytest.raises(ValueError):
            parse_key(bad)


def test_sha256_hasher_produces_self_describing_key() -> None:
    key = Sha256Hasher().hash(io.BytesIO(b""))
    assert key == f"sha256:{_EMPTY_SHA256}"
    assert algorithm_of(key) == "sha256"


def test_sha256_hasher_streams_in_chunks() -> None:
    payload = b"sartre" * 100_000  # larger than one read chunk
    once = Sha256Hasher().hash(io.BytesIO(payload))
    again = Sha256Hasher().hash(io.BytesIO(payload))
    assert once == again
    assert once.startswith("sha256:")
