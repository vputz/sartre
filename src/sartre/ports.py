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

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, BinaryIO, Protocol, runtime_checkable

from sartre.model import HEAD, Coordinate, Entry, Hash, Ref, Snapshot, Version


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
        """Return the known immutable versions of a coordinate."""
        ...

    def commit(
        self, coord: Coordinate, entries: Iterable[Entry], metadata: Mapping[str, Any]
    ) -> Version:
        """Record a new immutable manifest version and return its id.

        Contract: committing does NOT advance any mutable pointer. Whether
        committing identical entries returns the same version
        (content-idempotency) depends on the version-id format and is
        deliberately left TBD pending that decision.
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
        """Open a blob for streaming reads, verifying integrity on download."""
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


@runtime_checkable
class BlobBackend(Protocol):
    """A dumb key/value byte store over opaque keys — no hashing, no CAS.

    The minimal surface a storage target must implement; :class:`CasStore`
    layers content-addressing and verification on top. An implementation MAY add
    an optional ``get_many(keys)`` batch hook for concurrent fetches.
    """

    def get(self, key: str) -> BinaryIO:
        """Open the value stored at ``key`` for streaming reads."""
        ...

    def put(self, key: str, data: BinaryIO) -> None:
        """Store ``data`` at ``key``."""
        ...

    def exists(self, key: str) -> bool:
        """Return whether ``key`` is present."""
        ...

    def delete(self, key: str) -> None:
        """Remove ``key``."""
        ...
