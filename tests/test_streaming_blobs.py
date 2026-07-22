"""Streaming blob tests: bounded-memory round-trip, the honest verify/seek split,
large-file publish, and concurrent identical-content convergence.

Grounds the add-streaming-blobs decisions: whole-blob reads verify, random-access
`open` does not, and publish/put stream without buffering the whole blob.
"""

from __future__ import annotations

import io
import threading
import tracemalloc
import uuid
from pathlib import Path
from typing import BinaryIO, cast

import fsspec
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sartre import (
    DEFAULT_HASHER,
    CasStore,
    Coordinate,
    FsspecBlobBackend,
    IntegrityError,
    MemoryRegistry,
    Repository,
)

COORD = Coordinate("models", "release")


def _mem_cas() -> CasStore:
    fs = fsspec.filesystem("memory")
    return CasStore(FsspecBlobBackend(fs, root=f"blobs-{uuid.uuid4().hex}"))


def _file_cas(root: Path) -> CasStore:
    return CasStore(FsspecBlobBackend(fsspec.filesystem("file"), root=str(root)))


def _repo(store: CasStore) -> Repository:
    return Repository(MemoryRegistry(), store, heartbeat_interval=3600.0)


# --- round-trip integrity (5.2) ---


@given(payload=st.binary(max_size=8192))
@settings(max_examples=40, deadline=None)
def test_put_get_roundtrip_random(payload: bytes, tmp_path_factory: pytest.TempPathFactory) -> None:
    store = _mem_cas()
    key = store.put(io.BytesIO(payload))
    assert key == DEFAULT_HASHER.hash(io.BytesIO(payload))
    dest = tmp_path_factory.mktemp("rt") / "out"
    assert store.get_to(key, dest).read_bytes() == payload  # verified whole-blob read
    assert store.open(key).read() == payload  # unverified seek path also returns it


@pytest.mark.parametrize("size", [0, 1, 65_535, 65_536, 65_537, 1 << 20, (1 << 20) + 1, 3 << 20])
def test_roundtrip_across_chunk_boundaries(size: int, tmp_path: Path) -> None:
    payload = b"\xa5" * size  # spans the hasher's 1 MiB chunking and copyfileobj's buffer
    store = _mem_cas()
    key = store.put(io.BytesIO(payload))
    assert store.get_to(key, tmp_path / "out").read_bytes() == payload


# --- honest read split (5.3) ---


def test_checkout_verifies_but_open_serves_corrupt_bytes(tmp_path: Path) -> None:
    store = _mem_cas()
    repo = _repo(store)
    repo.publish(COORD, {"w.bin": b"WEIGHTS", "cfg.json": b"{}"})
    snap = repo.resolve(COORD)
    target = next(e for e in snap.entries if e.path == "w.bin")

    backend = cast(FsspecBlobBackend, store.backend)  # corrupt the stored blob in place
    with cast(BinaryIO, backend.fs.open(backend._path(target.content_hash), "wb")) as handle:
        handle.write(b"XXXXXXX")

    with pytest.raises(IntegrityError):  # whole-blob materialization verifies → rejects
        repo.checkout(snap, tmp_path / "out")
    # random-access open() is unverified: it serves the corrupt bytes without raising
    assert store.open(target.content_hash).read() == b"XXXXXXX"


# --- large-file publish in bounded memory (5.4) ---


def test_publish_path_streams_in_bounded_memory(tmp_path: Path) -> None:
    size = 8 << 20  # 8 MiB on disk
    big = tmp_path / "big.bin"
    big.write_bytes(b"\x5a" * size)
    store = _file_cas(tmp_path / "blobs")  # disk-backed, so the blob is not held in RAM
    repo = _repo(store)

    tracemalloc.start()
    tracemalloc.reset_peak()
    repo.publish(COORD, {"big.bin": big})
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < size // 2, f"peak {peak} suggests the whole {size}-byte blob was buffered"

    snap = repo.resolve(COORD)
    assert repo.open(snap, "big.bin").read_bytes() == b"\x5a" * size


# --- concurrent identical-content convergence (5.5) ---


def test_concurrent_identical_content_converges() -> None:
    store = _mem_cas()
    payload = b"the-same-content-from-many-writers"
    keys: list[str] = []
    keys_lock = threading.Lock()
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()  # maximise the promote race
        key = store.put(io.BytesIO(payload))
        with keys_lock:
            keys.append(key)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(set(keys)) == 1  # all writers agree on the content hash
    assert list(store.list()) == [keys[0]]  # blob present exactly once


# --- publish accepts bytes and path sources (5.6) ---


def test_publish_accepts_bytes_and_path_sources(tmp_path: Path) -> None:
    on_disk = tmp_path / "model.bin"
    on_disk.write_bytes(b"PATH-WEIGHTS")
    store = _mem_cas()
    repo = _repo(store)

    repo.publish(COORD, {"a.bin": b"BYTES-DATA", "b.bin": on_disk})
    snap = repo.resolve(COORD)
    sizes = {e.path: e.size for e in snap.entries}
    assert sizes == {"a.bin": len(b"BYTES-DATA"), "b.bin": len(b"PATH-WEIGHTS")}
    assert repo.open(snap, "a.bin").read_bytes() == b"BYTES-DATA"
    assert repo.open(snap, "b.bin").read_bytes() == b"PATH-WEIGHTS"
