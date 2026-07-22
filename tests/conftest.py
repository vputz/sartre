"""Shared fixtures for the reference-backend tests.

fsspec's in-memory filesystem is process-global, so every `CasStore` is rooted at
a unique prefix to keep tests isolated.
"""

from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator

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


@pytest.fixture(scope="session")
def postgres_dsn() -> Iterator[str]:
    """A throwaway dockerized Postgres, skipped when docker/psycopg are unavailable."""
    if shutil.which("docker") is None:
        pytest.skip("docker not available")
    try:
        import psycopg
    except ImportError:
        pytest.skip("psycopg not installed (postgres extra)")

    run = subprocess.run(
        ["docker", "run", "-d", "-e", "POSTGRES_PASSWORD=sartre",
         "-e", "POSTGRES_DB=sartre", "-P", "postgres:16"],
        capture_output=True, text=True,
    )
    if run.returncode != 0:
        pytest.skip(f"could not start postgres container: {run.stderr.strip()}")
    container = run.stdout.strip()
    try:
        mapping = subprocess.run(
            ["docker", "port", container, "5432"], capture_output=True, text=True
        ).stdout.strip()
        port = mapping.rsplit(":", 1)[-1]
        dsn = f"postgresql://postgres:sartre@localhost:{port}/sartre"
        for _ in range(60):
            try:
                psycopg.connect(dsn).close()
                break
            except Exception:  # noqa: BLE001 - poll until ready
                time.sleep(1)
        else:
            pytest.skip("postgres did not become ready in time")
        yield dsn
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
