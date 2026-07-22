"""The core ports: ``Registry`` (manifest plane) and ``Store`` / ``BlobBackend``
(blob plane).

These Protocols are the stable interface skeleton. ``Repository`` composes a
``Registry`` and a ``Store``; ``CasStore`` adapts a dumb ``BlobBackend`` into a
content-addressed ``Store``. The contracts in the docstrings (cheap ``head``,
atomic ``resolve``, compare-and-swap ``set_pointer``, verify-on-download,
thread-safety) are obligations every adapter must uphold — they are not
expressible in the signatures, so they live here as documented invariants.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence, Set
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, NewType, Protocol, runtime_checkable

from sartre.model import HEAD, Coordinate, Entry, Hash, Ref, Snapshot, Version

LeaseId = NewType("LeaseId", int)
"""Opaque token identifying a live lease (see :meth:`Registry.acquire_lease`)."""

DEFAULT_LEASE_TTL = 300.0
"""Default lease time-to-live, in seconds. A publish renews well within this on a
heartbeat; it is a liveness/throughput knob, not a safety parameter (safety comes
from the pre-commit/pre-advance self-check — see the repository-facade capability)."""


@dataclass(frozen=True, slots=True)
class LogEntry:
    """One commit-log row: a version that became a tip, with its order and time."""

    version: Version
    seq: int
    created_at: datetime


@runtime_checkable
class Registry(Protocol):
    """The manifest plane: names → versions → manifests. Never sees blob bytes."""

    def head(self, coord: Coordinate, ref: Ref = HEAD) -> Version:
        """Return the version a ref currently resolves to.

        Contract: this MUST be a single cheap pointer read — it MUST NOT fetch
        or scan a manifest. It is the polling primitive.
        """
        ...

    def resolve(self, coord: Coordinate, ref: Ref = HEAD) -> Snapshot:
        """Resolve a ref to an immutable :class:`Snapshot` (manifest, no bytes).

        Contract: atomic — never returns a partially published manifest. Raises
        :class:`~sartre.errors.NotFound` if the ref cannot be resolved.
        """
        ...

    def list_pointers(self, coord: Coordinate) -> Mapping[str, Version]:
        """Return the mutable pointers of a coordinate as ``name -> version``."""
        ...

    def list_versions(self, coord: Coordinate) -> Sequence[Version]:
        """Return the coordinate's versions in commit-log order (oldest first).

        Contract: because version ids are content hashes and carry no intrinsic
        order, ordering is the commit log's authoritative sequence, not anything
        derived from the version values.
        """
        ...

    def commit(
        self, coord: Coordinate, entries: Iterable[Entry], metadata: Mapping[str, Any]
    ) -> Version:
        """Record a new immutable manifest version and return its id.

        Contract: committing does NOT advance any mutable pointer. Committing is
        content-idempotent — committing the same ``(path, content_hash)`` entries
        returns the same :data:`~sartre.model.Version` and stores no duplicate
        manifest, regardless of ``metadata``, entry order, or coordinate (the
        version is the content hash of the manifest; see
        :func:`sartre.hashing.manifest_version`).
        """
        ...

    def set_pointer(
        self, coord: Coordinate, name: str, version: Version, *, expected: Version | None
    ) -> None:
        """Atomically advance a mutable pointer via compare-and-swap.

        Contract: the pointer advances only if its current value equals
        ``expected`` (``None`` means "must not currently exist"). On mismatch,
        raise :class:`~sartre.errors.Conflict` and leave the pointer unchanged.
        """
        ...

    # --- enumeration & lifecycle (for garbage collection) ---

    def list_coordinates(self) -> Sequence[Coordinate]:
        """Return every coordinate the registry holds (repo-wide root discovery)."""
        ...

    def list_log(self, coord: Coordinate) -> Sequence[LogEntry]:
        """Return the coordinate's commit log in sequence order (oldest first).

        Each :class:`LogEntry` carries ``(version, seq, created_at)``, so a garbage
        collector can apply ``keep_last_n`` (by order) and ``keep_within`` (by time).
        """
        ...

    def drop_version(self, version: Version) -> None:
        """Remove a manifest record that retention no longer wants (repo-wide).

        Contract: manifests are global and content-addressed (a version may be
        shared across coordinates by promotion), so this is repository-wide. It
        MUST raise :class:`~sartre.errors.Conflict` if the version is the current
        target of any pointer in any coordinate; otherwise it prunes the version
        from every coordinate's log and removes the manifest record. Idempotent:
        dropping an absent version is a no-op.
        """
        ...

    def acquire_lease(
        self, version: Version, hashes: Set[Hash], ttl: float = DEFAULT_LEASE_TTL
    ) -> LeaseId:
        """Claim a version and its blob hashes as GC roots for an in-flight write.

        Contract: a lease is not a lock — it grants no exclusive access. The lease is
        valid for ``ttl`` seconds against the **registry's own clock**; while it is live
        the version appears in :meth:`active_leased_versions` and the hashes in
        :meth:`active_leased_hashes`, so a concurrent GC treats them as roots. Once its
        ttl elapses without a :meth:`renew_lease`, the lease expires and stops
        protecting — so a crashed holder cannot pin storage forever. The lease is durable
        (visible to every process sharing the registry), not process-scoped.
        """
        ...

    def renew_lease(self, lease_id: LeaseId, ttl: float = DEFAULT_LEASE_TTL) -> bool:
        """Extend a still-valid lease's expiry to ``now + ttl``; report whether it held.

        Contract: evaluated against the registry clock. Returns ``True`` and extends the
        lease if it exists and has not yet expired; returns ``False`` and revives nothing
        if the lease is unknown or already expired. This doubles as the publish
        **self-check**: a ``False`` return tells an in-flight publish its lease lapsed and
        it must abort rather than commit or advance over possibly-reclaimed blobs.
        """
        ...

    def release_lease(self, lease_id: LeaseId) -> None:
        """Release a lease. Idempotent: releasing an unknown lease is a no-op."""
        ...

    def active_leased_hashes(self) -> Set[Hash]:
        """The union of blob hashes under all live (unexpired) leases."""
        ...

    def active_leased_versions(self) -> Set[Version]:
        """The union of versions under all live (unexpired) leases."""
        ...


@runtime_checkable
class Store(Protocol):
    """The blob plane: content-hash ↔ bytes. Never sees names or versions.

    Stream/path-oriented by contract: methods MUST NOT require materializing a
    whole blob into a ``bytes`` value, so multi-hundred-megabyte blobs work.
    Implementations MUST be safe to call concurrently from multiple threads.
    """

    def has(self, content_hash: Hash) -> bool:
        """Return whether a blob with this content hash is present."""
        ...

    def open(self, content_hash: Hash) -> BinaryIO:
        """Open a blob for seekable, random-access reads.

        Contract: returns a seekable handle for random access (e.g. a parquet-footer
        seek). This handle is NOT integrity-verified per read — a partial read cannot be
        checked against a whole-blob content hash. Verified bytes come from
        :meth:`get_to` (whole-blob) or from a :class:`~sartre.store.CachingStore`
        (verified on local materialization).
        """
        ...

    def get_to(self, content_hash: Hash, dest: Path) -> Path:
        """Materialize a blob to a local path and return it."""
        ...

    def put(self, data: BinaryIO) -> Hash:
        """Store bytes and return their self-describing content hash. Idempotent."""
        ...

    def delete(self, content_hash: Hash) -> None:
        """Remove a blob by content hash (for garbage collection)."""
        ...

    def list(self) -> Iterable[Hash]:
        """Enumerate the content hashes of all stored blobs (the GC sweep domain)."""
        ...

    def mtime(self, content_hash: Hash) -> float | None:
        """Return the blob's last-modification time as epoch seconds, or ``None``.

        Used by the GC grace-period backstop to retain recently-written blobs. ``None``
        means the backend cannot report a mtime (grace protection then does not apply to
        that blob).
        """
        ...


@runtime_checkable
class BlobBackend(Protocol):
    """A dumb key/value byte store over opaque keys — no hashing, no CAS.

    The minimal surface a storage target must implement; :class:`CasStore`
    layers content-addressing and verification on top. An implementation MAY add
    an optional ``get_many(keys)`` batch hook for concurrent fetches.
    """

    def get(self, key: str) -> BinaryIO:
        """Open the value stored at ``key`` for seekable, streaming reads."""
        ...

    def stage(self, data: BinaryIO) -> str:
        """Stream ``data`` to a reserved staging key and return it.

        Contract: the single write entry point — there is no key-first ``put`` (a
        content-addressed writer does not know the key until the bytes are hashed).
        ``stage`` MUST consume ``data`` incrementally (bounded memory) and MUST NOT make
        the bytes visible at any content key; they appear only after :meth:`promote`.
        """
        ...

    def promote(self, staging_key: str, final_key: str) -> None:
        """Atomically make the staged bytes appear at ``final_key``.

        Contract: a blob appears at ``final_key`` only once fully written (a rename, not
        a copy-in-place). Idempotent: if ``final_key`` already exists (identical content),
        the staged object is discarded rather than overwritten.
        """
        ...

    def exists(self, key: str) -> bool:
        """Return whether ``key`` is present."""
        ...

    def delete(self, key: str) -> None:
        """Remove ``key``."""
        ...

    def list(self) -> Iterable[str]:
        """Enumerate all stored keys (surfaced by ``CasStore.list`` as content hashes)."""
        ...

    def mtime(self, key: str) -> float | None:
        """Return ``key``'s last-modification time as epoch seconds, or ``None``."""
        ...
