"""Tests for the durable SqliteRegistry and open_local.

The differential stateful machine drives identical random operation sequences
against both `MemoryRegistry` (the TLA-backed reference) and `SqliteRegistry`,
asserting observationally equal results and equivalent raised errors — equivalence
to the verified reference is the correctness argument.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from sartre import (
    DEFAULT_HASHER,
    HEAD,
    Conflict,
    Coordinate,
    Entry,
    MemoryRegistry,
    NotFound,
    Pin,
    SqliteRegistry,
    open_local,
)
from sartre.model import Version

COORD = Coordinate("models", "release")


def _entry(path: str, data: bytes) -> Entry:
    return Entry(path=path, content_hash=DEFAULT_HASHER.hash(io.BytesIO(data)), size=len(data))


# --- targeted cases ---


def test_commit_is_idempotent() -> None:
    reg = SqliteRegistry(":memory:")
    entries = (_entry("a.txt", b"one"),)
    v1 = reg.commit(COORD, entries, {})
    v2 = reg.commit(COORD, entries, {})
    assert v1 == v2


def test_set_pointer_cas_success_and_conflict() -> None:
    reg = SqliteRegistry(":memory:")
    v1 = reg.commit(COORD, (_entry("a.txt", b"one"),), {})
    v2 = reg.commit(COORD, (_entry("a.txt", b"two"),), {})
    reg.set_pointer(COORD, "head", v1, expected=None)
    assert reg.head(COORD) == v1
    try:
        reg.set_pointer(COORD, "head", v2, expected=None)  # stale expected
        raise AssertionError("expected Conflict")
    except Conflict:
        pass
    assert reg.head(COORD) == v1  # unchanged
    reg.set_pointer(COORD, "head", v2, expected=v1)  # correct CAS
    assert reg.head(COORD) == v2


def test_pin_and_not_found() -> None:
    reg = SqliteRegistry(":memory:")
    v1 = reg.commit(COORD, (_entry("a.txt", b"one"),), {})
    reg.set_pointer(COORD, "head", v1, expected=None)
    v2 = reg.commit(COORD, (_entry("a.txt", b"two"),), {})
    reg.set_pointer(COORD, "head", v2, expected=v1)
    assert reg.resolve(COORD, Pin(v1)).version == v1  # old version still pinnable
    try:
        reg.resolve(COORD, Pin("sha256:" + "0" * 64))
        raise AssertionError("expected NotFound")
    except NotFound:
        pass


def test_drop_version_refuses_pointer_target_and_prunes() -> None:
    reg = SqliteRegistry(":memory:")
    v1 = reg.commit(COORD, (_entry("a.txt", b"one"),), {})
    reg.set_pointer(COORD, "head", v1, expected=None)
    v2 = reg.commit(COORD, (_entry("a.txt", b"two"),), {})
    reg.set_pointer(COORD, "head", v2, expected=v1)  # tip is now v2; v1 is history

    try:
        reg.drop_version(v2)  # current tip
        raise AssertionError("expected Conflict")
    except Conflict:
        pass
    reg.drop_version(v1)  # old history, no pointer -> dropped
    assert [e.version for e in reg.list_log(COORD)] == [v2]
    try:
        reg.resolve(COORD, Pin(v1))
        raise AssertionError("v1 should be gone")
    except NotFound:
        pass
    reg.drop_version(v1)  # idempotent no-op


def test_lease_acquire_renew_release() -> None:
    reg = SqliteRegistry(":memory:")
    lease = reg.acquire_lease("sha256:v", {"sha256:h1", "sha256:h2"}, ttl=100.0)
    assert reg.active_leased_hashes() == {"sha256:h1", "sha256:h2"}
    assert reg.active_leased_versions() == {"sha256:v"}
    assert reg.renew_lease(lease, ttl=100.0) is True  # still valid → extends
    reg.release_lease(lease)
    assert reg.active_leased_hashes() == set()
    assert reg.renew_lease(lease, ttl=100.0) is False  # released → unknown


def test_lease_expiry_drops_from_active_set() -> None:
    reg = SqliteRegistry(":memory:")
    # A non-positive ttl makes expires_at <= now deterministically (registry clock).
    expired = reg.acquire_lease("sha256:x", {"sha256:h"}, ttl=-1.0)
    assert reg.active_leased_hashes() == set()  # already lapsed → not a root
    assert reg.active_leased_versions() == set()
    assert reg.renew_lease(expired, ttl=100.0) is False  # cannot revive a lapsed lease


def test_memory_lease_ttl_with_injected_clock() -> None:
    now = [1000.0]
    reg = MemoryRegistry(clock=lambda: now[0])
    lease = reg.acquire_lease("sha256:v", {"sha256:h"}, ttl=10.0)  # expires at 1010
    assert reg.active_leased_versions() == {"sha256:v"}
    now[0] = 1005.0
    assert reg.renew_lease(lease, ttl=10.0) is True  # valid at 1005 → now expires at 1015
    now[0] = 1020.0  # past the renewed expiry
    assert reg.active_leased_versions() == set()
    assert reg.active_leased_hashes() == set()
    assert reg.renew_lease(lease, ttl=10.0) is False  # lapsed → cannot revive


def test_postgres_multiwriter_lease_visibility(postgres_dsn: str) -> None:
    """A lease taken by one registry instance is a GC root for another over the same DSN."""
    from sartre import PostgresRegistry

    writer = PostgresRegistry(postgres_dsn)
    writer._conn.execute("TRUNCATE leases RESTART IDENTITY")
    gc_view = PostgresRegistry(postgres_dsn)  # a separate connection, as a GC process would have
    try:
        lease = writer.acquire_lease("sha256:v", {"sha256:a", "sha256:b"}, ttl=100.0)
        # The GC-side instance sees the other writer's in-flight lease (durable, shared).
        assert gc_view.active_leased_hashes() == {"sha256:a", "sha256:b"}
        assert gc_view.active_leased_versions() == {"sha256:v"}
        writer.release_lease(lease)
        assert gc_view.active_leased_hashes() == set()
        # An already-expired lease never protects, even cross-instance.
        writer.acquire_lease("sha256:w", {"sha256:c"}, ttl=-1.0)
        assert gc_view.active_leased_hashes() == set()
    finally:
        writer.close()
        gc_view.close()


# --- durability ---


def test_state_survives_restart(tmp_path: Path) -> None:
    dbfile = tmp_path / "registry.db"
    reg = SqliteRegistry(dbfile)
    entries = (_entry("a.txt", b"one"), _entry("b/c.txt", b"two"))
    version = reg.commit(COORD, entries, {"note": "v1"})
    reg.set_pointer(COORD, "head", version, expected=None)
    reg.acquire_lease(version, {"sha256:held"}, ttl=300.0)  # durable, within ttl
    reg.close()

    reopened = SqliteRegistry(dbfile)
    assert reopened.head(COORD) == version
    snap = reopened.resolve(COORD)
    assert snap.version == version
    assert {e.path for e in snap.entries} == {"a.txt", "b/c.txt"}
    assert snap.metadata == {"note": "v1"}
    # Leases are now durable (registry-side): the still-valid lease survives the reopen,
    # so multi-writer GC on any instance sees it. It is reclaimed by ttl expiry, not exit.
    assert reopened.active_leased_versions() == {version}


def test_open_local_round_trips_across_reopen(tmp_path: Path) -> None:
    repo = open_local(tmp_path / "repo")
    version = repo.publish(COORD, {"w.bin": b"WEIGHTS", "cfg.json": b"{}"})

    reopened = open_local(tmp_path / "repo")  # fresh registry + store on same dir
    snap = reopened.resolve(COORD)
    assert snap.version == version
    assert reopened.open(snap, "w.bin").read_bytes() == b"WEIGHTS"


# --- differential stateful machine ---

_names = st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=3)
_filesets = st.dictionaries(_names, st.binary(max_size=8), min_size=1, max_size=3)
_meta = st.dictionaries(_names, st.integers(min_value=0, max_value=9), max_size=2)
_coord_index = st.integers(min_value=0, max_value=1)
_pointer = st.sampled_from(["head", "prod"])


def _attempt(fn: Callable[[], Any]) -> tuple[str, Any]:
    try:
        return ("ok", fn())
    except (Conflict, NotFound) as exc:
        return ("err", type(exc).__name__)


def _resolve_norm(reg: Any, coord: Coordinate, ref: Any) -> Any:
    try:
        snap = reg.resolve(coord, ref)
    except NotFound:
        return ("err",)
    entries = sorted((e.path, e.content_hash, e.size, e.inline) for e in snap.entries)
    return (snap.version, entries, dict(snap.metadata))


class DiffMachine(RuleBasedStateMachine):
    def _new_sql(self) -> Any:
        return SqliteRegistry(":memory:")

    def __init__(self) -> None:
        super().__init__()
        self.mem = MemoryRegistry()
        self.sql = self._new_sql()
        self.coords = [Coordinate("a", "dev"), Coordinate("a", "prod")]
        self.versions: list[Version] = []
        self.expected: dict[tuple[int, str], Version] = {}
        self.leases: list[tuple[Any, Any]] = []  # (memory lease id, sql lease id) pairs

    def teardown(self) -> None:
        self.sql.close()

    @rule(files=_filesets, meta=_meta)
    def commit(self, files: dict[str, bytes], meta: dict[str, int]) -> None:
        entries = tuple(_entry(p, v) for p, v in files.items())
        v_mem = self.mem.commit(self.coords[0], entries, meta)
        v_sql = self.sql.commit(self.coords[0], entries, meta)
        assert v_mem == v_sql
        if v_mem not in self.versions:
            self.versions.append(v_mem)

    def _both_set_pointer(
        self, coord: Coordinate, name: str, version: Version, expected: Version | None
    ) -> tuple[str, Any]:
        out_mem = _attempt(lambda: self.mem.set_pointer(coord, name, version, expected=expected))
        out_sql = _attempt(lambda: self.sql.set_pointer(coord, name, version, expected=expected))
        assert out_mem == out_sql
        return out_mem

    @rule(ci=_coord_index, name=_pointer, vi=st.integers(min_value=0, max_value=9))
    def set_pointer_valid(self, ci: int, name: str, vi: int) -> None:
        if not self.versions:
            return
        version = self.versions[vi % len(self.versions)]
        key = (ci, name)
        out = self._both_set_pointer(self.coords[ci], name, version, self.expected.get(key))
        if out[0] == "ok":
            self.expected[key] = version

    @rule(
        ci=_coord_index,
        name=_pointer,
        vi=st.integers(min_value=0, max_value=9),
        ei=st.integers(min_value=0, max_value=9),
    )
    def set_pointer_maybe_stale(self, ci: int, name: str, vi: int, ei: int) -> None:
        if not self.versions:
            return
        version = self.versions[vi % len(self.versions)]
        pool: list[Version | None] = [None, *self.versions]
        wrong = pool[ei % len(pool)]
        key = (ci, name)
        out = self._both_set_pointer(self.coords[ci], name, version, wrong)
        if out[0] == "ok":
            self.expected[key] = version

    @rule(vi=st.integers(min_value=0, max_value=9))
    def drop_version(self, vi: int) -> None:
        if not self.versions:
            return
        version = self.versions[vi % len(self.versions)]
        out_mem = _attempt(lambda: self.mem.drop_version(version))
        out_sql = _attempt(lambda: self.sql.drop_version(version))
        assert out_mem == out_sql
        if out_mem[0] == "ok":
            self.versions.remove(version)

    @rule(vi=st.integers(min_value=0, max_value=9), hs=st.sets(_names, max_size=3))
    def acquire_lease(self, vi: int, hs: set[str]) -> None:
        # Lease ids are backend-assigned opaque tokens (memory counter vs SQL autoincrement),
        # so they need not match; observational equivalence is the active-set invariant. Both
        # take the default (long) ttl, so nothing expires mid-sequence and the sets stay equal.
        version = self.versions[vi % len(self.versions)] if self.versions else "sha256:none"
        hashes = {f"sha256:{h}" for h in hs}
        lid_mem = self.mem.acquire_lease(version, hashes)
        lid_sql = self.sql.acquire_lease(version, hashes)
        self.leases.append((lid_mem, lid_sql))

    @rule(li=st.integers(min_value=0, max_value=9))
    def release_lease(self, li: int) -> None:
        if not self.leases:
            return
        lid_mem, lid_sql = self.leases.pop(li % len(self.leases))
        self.mem.release_lease(lid_mem)
        self.sql.release_lease(lid_sql)

    @invariant()
    def backends_agree(self) -> None:
        assert {(c.name, c.env) for c in self.mem.list_coordinates()} == {
            (c.name, c.env) for c in self.sql.list_coordinates()
        }
        assert self.mem.active_leased_hashes() == self.sql.active_leased_hashes()
        assert self.mem.active_leased_versions() == self.sql.active_leased_versions()
        for coord in self.coords:
            assert dict(self.mem.list_pointers(coord)) == dict(self.sql.list_pointers(coord))
            assert list(self.mem.list_versions(coord)) == list(self.sql.list_versions(coord))
            assert [e.version for e in self.mem.list_log(coord)] == [
                e.version for e in self.sql.list_log(coord)
            ]
            assert _attempt(lambda c=coord: self.mem.head(c)) == _attempt(  # type: ignore[misc]
                lambda c=coord: self.sql.head(c)
            )
            assert _resolve_norm(self.mem, coord, HEAD) == _resolve_norm(self.sql, coord, HEAD)


DiffMachine.TestCase.settings = settings(max_examples=50, stateful_step_count=30, deadline=None)
TestDiffMachine = DiffMachine.TestCase


class PostgresDiffMachine(DiffMachine):
    """Same differential machine, but the SQL side is a real Postgres."""

    _dsn = ""

    def _new_sql(self) -> Any:
        from sartre import PostgresRegistry

        reg = PostgresRegistry(self._dsn)
        reg._conn.execute("TRUNCATE manifests, entries, pointers, log, leases RESTART IDENTITY")
        return reg


def test_postgres_differential_equivalence(postgres_dsn: str) -> None:
    from hypothesis.stateful import run_state_machine_as_test

    PostgresDiffMachine._dsn = postgres_dsn
    run_state_machine_as_test(
        PostgresDiffMachine,
        settings=settings(max_examples=25, stateful_step_count=20, deadline=None),
    )
