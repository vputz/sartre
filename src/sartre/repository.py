"""``Repository`` facade and the ``AsyncRepository`` wrapper.

``Repository`` composes a ``Registry`` and a ``Store`` and implements the read
core (`head`/`resolve`/`open`/`fetch_all`/`snapshot_fs`/`checkout`) and the write
path (`publish`). Multi-file fetches parallelize over a thread pool (blob I/O
releases the GIL). ``AsyncRepository`` offers awaitable equivalents by offloading
to a thread.
"""

from __future__ import annotations

import asyncio
import io
import tempfile
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sartre.errors import Conflict, LeaseExpired, NotFound, PathError
from sartre.fs import SnapshotFS
from sartre.hashing import DEFAULT_HASHER, Hasher, manifest_version
from sartre.model import HEAD, Alias, Coordinate, Entry, Hash, Head, Pin, Ref, Snapshot, Version
from sartre.paths import check_no_case_collisions, normalize_path
from sartre.ports import DEFAULT_LEASE_TTL, Registry, Store


def _pointer_ref(pointer: str) -> Ref:
    return Head() if pointer == "head" else Alias(pointer)


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """What GC keeps beyond the always-protected pointers/tags.

    ``keep_last_n`` retains the newest N distinct versions per coordinate (by
    commit-log order); ``keep_within`` retains versions whose log time is within
    the duration of ``now``. ``grace`` is a blob-only backstop: GC retains any blob
    whose store mtime is younger than ``grace``, protecting the put→commit window of a
    writer that took no lease (correctness rests on ``grace`` exceeding the max
    publish duration). The default (``0`` / ``None``) keeps only pointers + live leases.
    """

    keep_last_n: int = 0
    keep_within: timedelta | None = None
    grace: timedelta | None = None


@dataclass(frozen=True, slots=True)
class GCResult:
    """What a GC pass reclaimed."""

    dropped_versions: tuple[Version, ...] = field(default_factory=tuple)
    deleted_blobs: tuple[Hash, ...] = field(default_factory=tuple)


class Repository:
    """Composes a :class:`Registry` and a :class:`Store` into the public surface."""

    def __init__(
        self,
        registry: Registry,
        store: Store,
        *,
        hasher: Hasher = DEFAULT_HASHER,
        lease_ttl: float = DEFAULT_LEASE_TTL,
        heartbeat_interval: float | None = None,
    ) -> None:
        self.registry = registry
        self.store = store
        self._hasher = hasher  # pre-hashes payloads so a publish can lease before uploading
        self._lease_ttl = lease_ttl
        # renew well within the ttl; halving leaves room for one missed beat before lapse.
        self._heartbeat_interval = heartbeat_interval or lease_ttl / 2

    def head(self, coord: Coordinate, ref: Ref = HEAD) -> Version:
        return self.registry.head(coord, ref)

    def resolve(self, coord: Coordinate, ref: Ref = HEAD) -> Snapshot:
        return self.registry.resolve(coord, ref)

    def _entry(self, snap: Snapshot, path: str) -> Entry:
        for entry in snap.entries:
            if entry.path == path:
                return entry
        raise NotFound(f"no entry {path!r} in {snap.coord} @ {snap.version}")

    def _materialize(self, entry: Entry, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if entry.inline is not None:  # small files served from the manifest row
            dest.write_bytes(entry.inline)
            return dest
        return self.store.get_to(entry.content_hash, dest)

    def open(self, snap: Snapshot, path: str) -> Path:
        """Materialize one entry's bytes to a local path, cached by content hash."""
        entry = self._entry(snap, path)
        dest = Path(tempfile.mkdtemp(prefix="sartre-open-")) / Path(path).name
        return self._materialize(entry, dest)

    def _layout(self, snap: Snapshot, root: Path, max_workers: int) -> Path:
        """Materialize every entry under ``root`` by logical path, in parallel.

        Each target is verified to stay within ``root`` (defense in depth — publish
        already forbids ``..``/absolute paths) before any bytes are written.
        """
        base = root.resolve()

        def place(entry: Entry) -> None:
            dest = (root / entry.path).resolve()
            if not dest.is_relative_to(base):
                raise PathError(f"entry {entry.path!r} escapes destination {root}")
            self._materialize(entry, dest)

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            list(pool.map(place, snap.entries))
        return root

    def fetch_all(self, snap: Snapshot, *, max_workers: int = 8) -> Path:
        """Materialize the whole snapshot to a fresh temp directory, in parallel."""
        root = Path(tempfile.mkdtemp(prefix="sartre-fetch-"))
        return self._layout(snap, root, max_workers)

    def checkout(self, snap: Snapshot, dest: Path, *, max_workers: int = 8) -> Path:
        """Materialize the whole snapshot under ``dest`` by logical path, in parallel.

        Files land at their canonical paths under ``dest`` and nothing is written
        outside it; uncached blobs are fetched concurrently and deduped by the store.
        """
        dest.mkdir(parents=True, exist_ok=True)
        return self._layout(snap, dest, max_workers)

    def snapshot_fs(self, snap: Snapshot) -> SnapshotFS:
        """A read-only fsspec filesystem bound to ``snap`` and this store."""
        return SnapshotFS(snap, self.store)

    def publish(
        self,
        coord: Coordinate,
        files: Mapping[str, bytes],
        *,
        pointer: str = "head",
        metadata: Mapping[str, object] | None = None,
    ) -> Version:
        """Full-replacement, fail-fast publish: lease → blobs → manifest → CAS pointer.

        The version and blob hashes are derived up front (payloads are pre-hashed), so the
        publish holds a lease over ``(version, hashes)`` before uploading — a concurrent GC
        treats the in-flight blobs as protected and the in-flight version as retained. A
        background heartbeat renews the lease within its ttl (liveness); and — the
        safety-critical step — the lease is re-verified live immediately before ``commit``
        and again before the pointer CAS, aborting with :class:`LeaseExpired` (retryable)
        if it lapsed, so a manifest is never committed or pointed over blobs GC may have
        reclaimed. See the repository-facade and garbage-collection capabilities.
        """
        # Canonicalize paths and reject case-collisions up front (write-time path model).
        normalized = {normalize_path(path): data for path, data in files.items()}
        check_no_case_collisions(normalized)

        # Pre-hash to derive entries and the version *before* uploading, so the lease
        # covers the upload → commit → advance window (store.put re-hashes; idempotent).
        entries = tuple(
            Entry(path=path, content_hash=self._hasher.hash(io.BytesIO(data)), size=len(data))
            for path, data in sorted(normalized.items())
        )
        version = manifest_version(entries, self._hasher)
        hashes = {entry.content_hash for entry in entries}

        try:
            start: Version | None = self.registry.head(coord, _pointer_ref(pointer))
        except NotFound:
            start = None  # first publish to this pointer

        lease = self.registry.acquire_lease(version, hashes, self._lease_ttl)
        stop = threading.Event()

        def _heartbeat() -> None:  # liveness only: keep a long publish's lease from lapsing
            while not stop.wait(self._heartbeat_interval):
                try:
                    self.registry.renew_lease(lease, self._lease_ttl)
                except Exception:  # noqa: BLE001 - registry gone; the self-check will catch it
                    return

        beat = threading.Thread(target=_heartbeat, name="sartre-lease-heartbeat", daemon=True)
        beat.start()
        try:
            for data in normalized.values():
                self.store.put(io.BytesIO(data))  # blobs first (idempotent, dedup by hash)
            if not self.registry.renew_lease(lease, self._lease_ttl):  # self-check before commit
                raise LeaseExpired("publish lease lapsed before commit; retry")
            committed = self.registry.commit(coord, entries, dict(metadata or {}))
            if not self.registry.renew_lease(lease, self._lease_ttl):  # self-check before advance
                raise LeaseExpired("publish lease lapsed before pointer advance; retry")
            self.registry.set_pointer(coord, pointer, committed, expected=start)  # CAS
            return committed
        finally:
            stop.set()
            beat.join(timeout=1.0)
            self.registry.release_lease(lease)

    # --- garbage collection ---

    def _distinct_log_versions(self, coord: Coordinate) -> list[Version]:
        seen: dict[Version, None] = {}  # newest-last, deduped
        for entry in self.registry.list_log(coord):
            seen[entry.version] = None
        return list(seen)

    def _retained_versions(self, policy: RetentionPolicy, now: datetime) -> set[Version]:
        """Versions protected from dropping: pointers/tags + keep_last_n + keep_within + leases."""
        retained: set[Version] = set(self.registry.active_leased_versions())
        for coord in self.registry.list_coordinates():
            retained.update(self.registry.list_pointers(coord).values())
            log = self.registry.list_log(coord)
            distinct = self._distinct_log_versions(coord)
            if policy.keep_last_n:
                retained.update(distinct[-policy.keep_last_n :])
            if policy.keep_within is not None:
                cutoff = now - policy.keep_within
                retained.update(e.version for e in log if e.created_at >= cutoff)
        return retained

    def _live_blobs(self) -> set[Hash]:
        """Blobs reachable from every resolvable version now, plus leased hashes."""
        live: set[Hash] = set(self.registry.active_leased_hashes())
        for coord in self.registry.list_coordinates():
            for version in self._distinct_log_versions(coord):
                snap = self.registry.resolve(coord, Pin(version))
                live.update(entry.content_hash for entry in snap.entries)
        return live

    def gc(
        self, policy: RetentionPolicy | None = None, *, clock: Callable[[], datetime] | None = None
    ) -> GCResult:
        """Mark-and-sweep unreferenced blobs and out-of-retention manifests.

        Mark bounds the candidate sets; the sweep RE-VALIDATES against current state
        before deleting (a blob marked sweepable can be reused under a fresh lease in
        the mark→sweep window — verified necessary by the GC TLA+ model). Idempotent
        and interrupt-safe. ``clock`` (default: ``datetime.now(UTC)``) drives
        ``keep_within`` and is injectable for deterministic tests.
        """
        policy = policy or RetentionPolicy()
        now = (clock or (lambda: datetime.now(UTC)))()

        # Mark: candidate manifests to drop and candidate blobs to sweep.
        retained = self._retained_versions(policy, now)
        drop_domain = {
            v
            for coord in self.registry.list_coordinates()
            for v in self._distinct_log_versions(coord)
        }
        candidate_versions = drop_domain - retained
        candidate_blobs = set(self.store.list())

        # Sweep, re-validated: drop only versions still unretained now; delete only
        # blobs referenced by no surviving manifest and no live lease now.
        dropped: list[Version] = []
        retained_now = self._retained_versions(policy, now)
        for version in candidate_versions:
            if version in retained_now:
                continue
            try:
                self.registry.drop_version(version)
                dropped.append(version)
            except Conflict:
                pass  # became a pointer target between mark and sweep; leave it

        live_now = self._live_blobs()
        grace_cutoff = now - policy.grace if policy.grace is not None else None
        deleted: list[Hash] = []
        for content_hash in candidate_blobs:
            if content_hash in live_now:
                continue
            if grace_cutoff is not None and self._within_grace(content_hash, grace_cutoff):
                continue  # blob-only backstop: too young to sweep (unleased-writer window)
            if self.store.has(content_hash):
                self.store.delete(content_hash)
                deleted.append(content_hash)

        return GCResult(dropped_versions=tuple(dropped), deleted_blobs=tuple(deleted))

    def _within_grace(self, content_hash: Hash, cutoff: datetime) -> bool:
        """Whether a blob's store mtime is at/after ``cutoff`` (younger than the grace window)."""
        mt = self.store.mtime(content_hash)
        if mt is None:  # backend can't report age → grace does not protect this blob
            return False
        return datetime.fromtimestamp(mt, UTC) >= cutoff


class AsyncRepository:
    """Awaitable wrapper that offloads the synchronous :class:`Repository` to a thread."""

    def __init__(self, repository: Repository) -> None:
        self._sync = repository

    async def head(self, coord: Coordinate, ref: Ref = HEAD) -> Version:
        return await asyncio.to_thread(self._sync.head, coord, ref)

    async def resolve(self, coord: Coordinate, ref: Ref = HEAD) -> Snapshot:
        return await asyncio.to_thread(self._sync.resolve, coord, ref)

    async def open(self, snap: Snapshot, path: str) -> Path:
        return await asyncio.to_thread(self._sync.open, snap, path)

    async def fetch_all(self, snap: Snapshot, *, max_workers: int = 8) -> Path:
        return await asyncio.to_thread(self._sync.fetch_all, snap, max_workers=max_workers)

    async def checkout(self, snap: Snapshot, dest: Path, *, max_workers: int = 8) -> Path:
        return await asyncio.to_thread(self._sync.checkout, snap, dest, max_workers=max_workers)

    async def gc(
        self, policy: RetentionPolicy | None = None, *, clock: Callable[[], datetime] | None = None
    ) -> GCResult:
        return await asyncio.to_thread(self._sync.gc, policy, clock=clock)
