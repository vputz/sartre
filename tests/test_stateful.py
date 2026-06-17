"""Stateful property test mirroring the publish-transaction TLA+ model.

This `RuleBasedStateMachine` drives randomized publish / promote / crash-and-retry
sequences against the live in-memory backend and asserts, after every step, the
invariants the TLA+ model proved abstractly
(`openspec/specs/publish-transaction/model/Publish.tla`):

- `PointerSafe` — every coordinate's tip resolves to a fully present manifest
  (no dangling reference), and its entries open to exactly the published bytes.
- convergence — a publish interrupted before the pointer advance (a simulated
  crash) leaves the tip unchanged; the durable orphan never corrupts reads.
"""

from __future__ import annotations

import io
import uuid

import fsspec
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from sartre import (
    CachingStore,
    CasStore,
    Coordinate,
    Entry,
    FsspecBlobBackend,
    MemoryRegistry,
    Repository,
)

_names = st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=4)
_filesets = st.dictionaries(_names, st.binary(max_size=32), min_size=1, max_size=4)
_coord_index = st.integers(min_value=0, max_value=1)


def _cas() -> CasStore:
    fs = fsspec.filesystem("memory")
    return CasStore(FsspecBlobBackend(fs, root=f"b-{uuid.uuid4().hex}"))


class PublishMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.repo = Repository(MemoryRegistry(), CachingStore(local=_cas(), remote=_cas()))
        self.coords = [Coordinate("m", "dev"), Coordinate("m", "release")]
        self.head: dict[tuple[str, str], str] = {}  # coord-key -> current tip version
        self.content: dict[str, dict[str, bytes]] = {}  # version -> {path: bytes}

    def _key(self, coord: Coordinate) -> tuple[str, str]:
        return (coord.name, coord.env)

    @rule(ci=_coord_index, files=_filesets)
    def publish(self, ci: int, files: dict[str, bytes]) -> None:
        coord = self.coords[ci]
        version = self.repo.publish(coord, files)
        self.head[self._key(coord)] = version
        self.content[version] = dict(files)

    @rule(src=_coord_index, dst=_coord_index)
    def promote(self, src: int, dst: int) -> None:
        s, d = self.coords[src], self.coords[dst]
        if self._key(s) not in self.head:
            return
        version = self.head[self._key(s)]
        self.repo.registry.set_pointer(
            d, "head", version, expected=self.head.get(self._key(d))
        )
        self.head[self._key(d)] = version

    @rule(ci=_coord_index, files=_filesets)
    def crash_before_pointer(self, ci: int, files: dict[str, bytes]) -> None:
        # Simulate a crash after blobs+manifest but before the pointer advance:
        # store blobs and commit, but never set_pointer. The tip must be untouched.
        coord = self.coords[ci]
        entries = tuple(
            Entry(p, self.repo.store.put(io.BytesIO(v)), len(v)) for p, v in files.items()
        )
        self.repo.registry.commit(coord, entries, {})  # orphan manifest; no pointer move

    @invariant()
    def tips_resolve_to_present_content(self) -> None:
        for (name, env), version in self.head.items():
            coord = Coordinate(name, env)
            assert self.repo.head(coord) == version
            snap = self.repo.resolve(coord)
            assert snap.version == version
            got = {e.path: self.repo.open(snap, e.path).read_bytes() for e in snap.entries}
            assert got == self.content[version]


PublishMachine.TestCase.settings = settings(
    max_examples=25, stateful_step_count=20, deadline=None
)
TestPublishMachine = PublishMachine.TestCase
