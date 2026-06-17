"""Shared fixtures for the reference-backend tests.

fsspec's in-memory filesystem is process-global, so every `CasStore` is rooted at
a unique prefix to keep tests isolated.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import fsspec
import pytest

from sartre import CachingStore, CasStore, FsspecBlobBackend, MemoryRegistry, Repository


def fresh_cas() -> CasStore:
    fs = fsspec.filesystem("memory")
    return CasStore(FsspecBlobBackend(fs, root=f"blobs-{uuid.uuid4().hex}"))


@pytest.fixture
def make_store() -> Callable[[], CasStore]:
    return fresh_cas


@pytest.fixture
def make_repo() -> Callable[[], Repository]:
    def _make() -> Repository:
        store = CachingStore(local=fresh_cas(), remote=fresh_cas())
        return Repository(MemoryRegistry(), store)

    return _make


@pytest.fixture
def repo(make_repo: Callable[[], Repository]) -> Repository:
    return make_repo()
