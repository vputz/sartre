"""Behavioral tests for the S3-native registry over a moto server (in-process).

Covers the load-bearing put-if-absent enforcement (task 1.1), the CAS pointer
advance, reads, enumeration, provenance, and drop + tombstone + history-forever.
Skips cleanly when s3fs / moto.server are unavailable.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from sartre import Alias, Conflict, Coordinate, Entry, NotFound, S3Registry
from sartre.model import Head

COORD = Coordinate("models", "prod")


@dataclass
class _S3Ctx:
    fs: object
    bucket: str
    registry: S3Registry
    opts: dict[str, Any]  # storage_options to reconstruct the fs via url_to_fs (for open_s3)


def _entries(tag: str) -> tuple[Entry, ...]:
    return (Entry(f"{tag}.bin", f"sha256:{tag * 4}", 3),)


@pytest.fixture
def s3() -> Iterator[_S3Ctx]:
    pytest.importorskip("s3fs")
    moto_server = pytest.importorskip("moto.server")
    import boto3
    import s3fs

    # moto's ThreadedMotoServer shares one process-global backend, so isolate per test
    # with a unique bucket rather than relying on a fresh server wiping state.
    bucket = f"reg-{uuid.uuid4().hex}"
    server = moto_server.ThreadedMotoServer(port=0)
    server.start()
    try:
        host, port = server.get_host_and_port()
        endpoint = f"http://{host}:{port}"
        boto3.client(
            "s3", endpoint_url=endpoint, aws_access_key_id="testing",
            aws_secret_access_key="testing", region_name="us-east-1",
        ).create_bucket(Bucket=bucket)  # us-east-1: no LocationConstraint
        opts: dict[str, Any] = {
            "key": "testing",
            "secret": "testing",
            "skip_instance_cache": True,
            "use_listings_cache": False,
            "client_kwargs": {"endpoint_url": endpoint, "region_name": "us-east-1"},
        }
        fs = s3fs.S3FileSystem(**opts)
        yield _S3Ctx(
            fs=fs, bucket=bucket, registry=S3Registry(fs, f"{bucket}/repo"), opts=opts
        )
    finally:
        server.stop()


@pytest.fixture
def registry(s3: _S3Ctx) -> S3Registry:
    return s3.registry


def test_put_if_absent_is_enforced(s3: _S3Ctx) -> None:
    """The whole CAS rests on this: a second exclusive-create must fail, body unchanged."""
    fs, key = s3.fs, f"{s3.bucket}/probe/x"
    with fs.open(key, "xb") as f:  # type: ignore[attr-defined]
        f.write(b"A")
    with pytest.raises(FileExistsError):
        with fs.open(key, "xb") as f:  # type: ignore[attr-defined]
            f.write(b"B")
    assert fs.cat_file(key) == b"A"  # type: ignore[attr-defined]


def test_commit_is_content_idempotent(registry: S3Registry) -> None:
    v1 = registry.commit(COORD, _entries("a"), {})
    v2 = registry.commit(COORD, _entries("a"), {"note": "different"})
    assert v1 == v2


def test_commit_does_not_set_a_pointer(registry: S3Registry) -> None:
    registry.commit(COORD, _entries("a"), {})
    with pytest.raises(NotFound):
        registry.head(COORD)


def test_set_pointer_cas_and_conflict(registry: S3Registry) -> None:
    v1 = registry.commit(COORD, _entries("a"), {})
    v2 = registry.commit(COORD, _entries("b"), {})
    registry.set_pointer(COORD, "head", v1, expected=None, actor="alice", reason="first")
    assert registry.head(COORD) == v1
    registry.set_pointer(COORD, "head", v2, expected=v1, actor="alice", reason="second")
    assert registry.head(COORD) == v2
    with pytest.raises(Conflict):  # stale expected — current is v2
        registry.set_pointer(COORD, "head", v1, expected=v1)
    assert registry.head(COORD) == v2


def test_set_pointer_refuses_uncommitted(registry: S3Registry) -> None:
    with pytest.raises(NotFound):
        registry.set_pointer(COORD, "head", "sha256:" + "0" * 8, expected=None)


def test_resolve_roundtrips_manifest(registry: S3Registry) -> None:
    entries = (Entry("w/model.bin", "sha256:" + "ab" * 4, 7, inline=b"WEIGHTS"),)
    v = registry.commit(COORD, entries, {"stage": "rc"})
    registry.set_pointer(COORD, "head", v, expected=None)
    snap = registry.resolve(COORD)
    assert snap.version == v
    assert dict(snap.metadata) == {"stage": "rc"}
    assert snap.entries[0].inline == b"WEIGHTS"


def test_enumeration_and_aliases(registry: S3Registry) -> None:
    v1 = registry.commit(COORD, _entries("a"), {})
    v2 = registry.commit(COORD, _entries("b"), {})
    registry.set_pointer(COORD, "head", v1, expected=None)
    registry.set_pointer(COORD, "head", v2, expected=v1)
    registry.set_pointer(COORD, "stable", v1, expected=None)
    assert registry.head(COORD, Alias("stable")) == v1
    assert registry.head(COORD, Head()) == v2
    assert dict(registry.list_pointers(COORD)) == {"head": v2, "stable": v1}
    assert set(registry.list_versions(COORD)) == {v1, v2}
    assert (COORD.name, COORD.env) in {(c.name, c.env) for c in registry.list_coordinates()}


def test_log_and_history_provenance(registry: S3Registry) -> None:
    v1 = registry.commit(COORD, _entries("a"), {})
    v2 = registry.commit(COORD, _entries("b"), {})
    registry.set_pointer(COORD, "head", v1, expected=None, actor="alice", reason="init")
    registry.set_pointer(COORD, "head", v2, expected=v1, actor="alice", reason="bump")
    registry.set_pointer(COORD, "stable", v1, expected=None, actor="bob", reason="promote")

    log = {(e.version, e.actor, e.reason) for e in registry.list_log(COORD)}
    assert (v1, "alice", "init") in log and (v1, "bob", "promote") in log
    moves = [
        (m.name, m.from_version, m.to_version, m.actor)
        for m in registry.list_pointer_history(COORD)
    ]
    assert ("head", None, v1, "alice") in moves
    assert ("head", v1, v2, "alice") in moves
    assert ("stable", None, v1, "bob") in moves


def test_drop_version_tombstones_and_keeps_history(registry: S3Registry) -> None:
    v1 = registry.commit(COORD, _entries("a"), {})
    v2 = registry.commit(COORD, _entries("b"), {})
    registry.set_pointer(COORD, "head", v1, expected=None, actor="a")
    registry.set_pointer(COORD, "head", v2, expected=v1, actor="a")  # v1 now history

    with pytest.raises(Conflict):
        registry.drop_version(v2)  # still the head target
    registry.drop_version(v1)  # out of retention → dropped

    assert v1 not in set(registry.list_versions(COORD))  # tombstoned → excluded
    assert any(e.version == v1 for e in registry.list_log(COORD))  # history is forever
    with pytest.raises(NotFound):
        registry.resolve(COORD, Alias("nope"))


def test_concurrent_cas_yields_exactly_one_winner(registry: S3Registry) -> None:
    """The core safety claim: racing advances from one start → one winner, rest Conflict."""
    import threading

    versions = [registry.commit(COORD, _entries(t), {}) for t in "abcd"]
    registry.set_pointer(COORD, "head", versions[0], expected=None)  # the shared start

    results: list[tuple[str, str]] = []
    lock = threading.Lock()

    def race(v: str) -> None:
        try:
            registry.set_pointer(COORD, "head", v, expected=versions[0])
            outcome = "ok"
        except Conflict:
            outcome = "conflict"
        with lock:
            results.append((outcome, v))

    threads = [threading.Thread(target=race, args=(versions[i],)) for i in (1, 2, 3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    winners = [v for outcome, v in results if outcome == "ok"]
    assert len(winners) == 1  # exactly one advanced
    assert results.count(("conflict", winners[0])) == 0
    assert sum(1 for o, _ in results if o == "conflict") == 2  # the losers observed a conflict
    assert registry.head(COORD) == winners[0]
    # gap-free: only the start event and the winner's event exist, contiguous
    keys = sorted(
        k for k in registry._list_names(registry._pointer_prefix(COORD, "head"))  # noqa: SLF001
        if k.endswith(".json")
    )
    assert keys == [f"{1:012d}.json", f"{2:012d}.json"]


def test_reads_revert_to_last_valid_when_tail_target_reclaimed(registry: S3Registry) -> None:
    """The S3Drop closure: a tombstoned tail target reverts reads to the prior valid version."""
    v1 = registry.commit(COORD, _entries("a"), {})
    v2 = registry.commit(COORD, _entries("b"), {})
    registry.set_pointer(COORD, "stable", v1, expected=None)
    registry.set_pointer(COORD, "stable", v2, expected=v1)  # stable history: [v1, v2]

    # simulate a drop of v2 winning a race against this promote: tombstone BEFORE delete
    registry._put_if_absent(registry._tombstone_path(v2), b"")  # noqa: SLF001
    registry.fs.rm(registry._manifest_path(v2))  # noqa: SLF001

    assert registry.head(COORD, Alias("stable")) == v1  # reverts, never dangles
    assert registry.resolve(COORD, Alias("stable")).version == v1  # a valid snapshot


def test_concurrent_promote_and_drop_never_dangles(registry: S3Registry) -> None:
    """Promote racing a drop: whoever wins, no pointer ever resolves to a reclaimed manifest."""
    import threading

    from sartre.model import Alias as _Alias

    v1 = registry.commit(COORD, _entries("a"), {})
    v2 = registry.commit(COORD, _entries("b"), {})
    registry.set_pointer(COORD, "head", v1, expected=None)
    registry.set_pointer(COORD, "head", v2, expected=v1)  # head=v2; v1 out of retention

    errors: list[Exception] = []

    def promote() -> None:
        try:
            registry.set_pointer(COORD, "stable", v1, expected=None, actor="p")
        except (Conflict, NotFound):
            pass  # lost the race — acceptable
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def drop() -> None:
        try:
            registry.drop_version(v1)
        except Conflict:
            pass  # v1 became a pointer target first — acceptable
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=promote), threading.Thread(target=drop)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors  # only expected typed failures
    try:  # the invariant: resolve yields a live manifest, or the pointer is unset
        snap = registry.resolve(COORD, _Alias("stable"))
        assert registry._exists(registry._manifest_path(snap.version))  # noqa: SLF001
    except NotFound:
        pass  # drop won and stable reverted to nothing — also safe


def test_differential_vs_memory_reference(s3: _S3Ctx) -> None:
    """S3Registry tracks the in-memory reference over a real op sequence (modulo tombstones)."""
    from sartre import MemoryRegistry
    from sartre.model import Head

    mem = MemoryRegistry()
    s3reg = s3.registry
    c = COORD

    def agree() -> None:
        assert set(mem.list_versions(c)) == set(s3reg.list_versions(c))
        assert dict(mem.list_pointers(c)) == dict(s3reg.list_pointers(c))
        for ref in (Head(), Alias("stable")):
            try:
                mv: object = mem.head(c, ref)
            except NotFound:
                mv = "notfound"
            try:
                sv: object = s3reg.head(c, ref)
            except NotFound:
                sv = "notfound"
            assert mv == sv

    va = mem.commit(c, _entries("a"), {})
    assert va == s3reg.commit(c, _entries("a"), {})
    vb = mem.commit(c, _entries("b"), {})
    assert vb == s3reg.commit(c, _entries("b"), {})

    mem.set_pointer(c, "head", va, expected=None, actor="x")
    s3reg.set_pointer(c, "head", va, expected=None, actor="x")
    agree()
    mem.set_pointer(c, "head", vb, expected=va, actor="x")
    s3reg.set_pointer(c, "head", vb, expected=va, actor="x")
    agree()
    mem.set_pointer(c, "stable", va, expected=None, actor="y")
    s3reg.set_pointer(c, "stable", va, expected=None, actor="y")
    agree()

    # stale CAS: both reject identically
    with pytest.raises(Conflict):
        mem.set_pointer(c, "head", va, expected=va)
    with pytest.raises(Conflict):
        s3reg.set_pointer(c, "head", va, expected=va)
    # drop a version both still point at: both refuse
    with pytest.raises(Conflict):
        mem.drop_version(va)
    with pytest.raises(Conflict):
        s3reg.drop_version(va)
    agree()

    # commit provenance agrees (order-independent set)
    prov_mem = {(e.version, e.actor) for e in mem.list_log(c)}
    prov_s3 = {(e.version, e.actor) for e in s3reg.list_log(c)}
    assert prov_mem == prov_s3


def test_open_s3_end_to_end(s3: _S3Ctx) -> None:
    from sartre import open_s3
    from sartre.model import Alias as _Alias

    repo = open_s3(f"s3://{s3.bucket}/repo", storage_options=s3.opts)
    v1 = repo.publish(COORD, {"w/model.bin": b"WEIGHTS", "cfg.json": b"{}"},
                      actor="alice", reason="init")
    (v2) = repo.publish(COORD, {"cfg.json": b'{"v":2}'}, actor="alice", reason="bump")
    assert v1 != v2
    snap = repo.resolve(COORD)
    assert snap.version == v2
    repo.point(COORD, "stable", v1, expected=None, actor="bob", reason="promote")
    assert repo.head(COORD, _Alias("stable")) == v1
    assert [(m.actor, m.to_version) for m in repo.list_pointer_history(COORD)][-1] == ("bob", v1)


def test_open_s3_verifies_lazily_on_first_write(s3: _S3Ctx) -> None:
    """Read-only usage must not trigger the (write+delete) conditional-write probe."""
    from sartre import open_s3

    repo = open_s3(f"s3://{s3.bucket}/repo2", storage_options=s3.opts)
    reg = repo.registry
    assert isinstance(reg, S3Registry) and reg._verified is False  # noqa: SLF001
    assert list(repo.list_coordinates()) == []  # a read does not verify
    assert reg._verified is False  # noqa: SLF001
    repo.publish(COORD, {"a.bin": b"1"}, actor="x")  # first write verifies
    assert reg._verified is True  # noqa: SLF001


def test_open_s3_rejects_non_conforming_endpoint() -> None:
    """A filesystem that does not enforce put-if-absent is rejected by the probe."""
    import io

    from sartre.errors import SartreError
    from sartre.s3 import _probe_conditional_writes

    class _NoCasFS:  # every 'xb' open "succeeds" — no conditional enforcement
        def open(self, path: str, mode: str):  # noqa: ANN202, ARG002
            return io.BytesIO()

        def rm(self, path: str) -> None:  # noqa: ARG002
            pass

    with pytest.raises(SartreError, match="put-if-absent"):
        _probe_conditional_writes(_NoCasFS(), "root")  # type: ignore[arg-type]
