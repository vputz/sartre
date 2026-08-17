"""``S3Registry``: a ``Registry`` on plain object storage, no database.

The manifest plane realized entirely as S3 objects, using **put-if-absent**
(``If-None-Match:*``, exposed by s3fs as ``open(key, "xb")``) as the only write
primitive — nothing is ever overwritten. Layout::

    {root}/manifests/<algo>/<digest>.json          immutable, content-addressed, shared
    {root}/coords/<name>/<env>/pointers/<p>/<seq>.json   append-only pointer-move events
    {root}/tombstones/<algo>/<digest>              "this version was GC-dropped"

A pointer is an append-only stream of immutable, zero-padded event objects; its
current value is the tail event. Advancing a pointer is a compare-and-swap: write
the next sequence number with put-if-absent — the loser of a race gets a
``FileExistsError`` (s3fs's surface of the 412) and retries. Enforcement is
server-side, so a stale client listing cache can never cause a lost write; the CAS
tail read nonetheless bypasses the listing cache so ``expected`` is judged against
the true current value. Correctness requires a backend that enforces put-if-absent
(real S3; verified emulators) — :func:`open_s3` probes for it.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Iterable, Mapping, Sequence, Set
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from fsspec import AbstractFileSystem

from sartre.errors import Conflict, NotFound, SartreError
from sartre.hashing import DEFAULT_HASHER, Hasher, manifest_version, parse_key
from sartre.model import HEAD, Coordinate, Entry, Hash, Pin, Ref, Snapshot, Version, pointer_name
from sartre.ports import DEFAULT_LEASE_TTL, LeaseId, LogEntry, PointerMove

if TYPE_CHECKING:
    from sartre.repository import Repository

_SEQ_WIDTH = 12  # zero-pad so lexical key order == numeric sequence order


class S3Registry:
    """Object-storage implementation of the `Registry` port (put-if-absent only)."""

    def __init__(
        self, fs: AbstractFileSystem, root: str, *, hasher: Hasher = DEFAULT_HASHER
    ) -> None:
        self.fs = fs
        self.root = root.rstrip("/")
        self._hasher = hasher
        self._verified = False  # conditional-write support is checked lazily on first write

    def _ensure_writable(self) -> None:
        """Verify put-if-absent support once, on the first write (never on read paths).

        Called from :meth:`_put_if_absent`, the single write chokepoint, so *every*
        mutation is guaranteed to run against a conforming endpoint.
        """
        if not self._verified:
            _probe_conditional_writes(self.fs, self.root)
            self._verified = True

    # --- key layout ---

    def _manifest_path(self, version: Version) -> str:
        algo, digest = parse_key(version)
        return f"{self.root}/manifests/{algo}/{digest}.json"

    def _tombstone_path(self, version: Version) -> str:
        algo, digest = parse_key(version)
        return f"{self.root}/tombstones/{algo}/{digest}"

    def _coord_root(self, coord: Coordinate) -> str:
        return f"{self.root}/coords/{coord.name}/{coord.env}"

    def _pointer_prefix(self, coord: Coordinate, name: str) -> str:
        return f"{self._coord_root(coord)}/pointers/{name}"

    def _event_path(self, coord: Coordinate, name: str, seq: int) -> str:
        return f"{self._pointer_prefix(coord, name)}/{seq:0{_SEQ_WIDTH}d}.json"

    # --- object primitives ---

    def _put_if_absent(self, path: str, body: bytes) -> bool:
        """Write ``body`` at ``path`` only if absent. True on write, False if it existed."""
        self._ensure_writable()  # every write funnels here → the endpoint is verified once
        try:
            with cast("Any", self.fs.open(path, "xb")) as handle:  # "xb" ⇒ If-None-Match:*
                handle.write(body)
            return True
        except FileExistsError:
            return False

    def _get_json(self, path: str) -> dict[str, Any] | None:
        try:
            return json.loads(self.fs.cat_file(path))
        except FileNotFoundError:
            return None

    def _list_names(self, prefix: str, *, refresh: bool = False) -> list[str]:
        """Basenames directly under ``prefix``; ``refresh`` bypasses the listing cache."""
        if refresh:
            self.fs.invalidate_cache(prefix)
        try:
            entries = self.fs.ls(prefix, detail=False)
        except FileNotFoundError:
            return []
        return [str(p).rstrip("/").rsplit("/", 1)[-1] for p in entries]

    def _exists(self, path: str) -> bool:
        return bool(self.fs.exists(path))

    # --- manifest ↔ json ---

    @staticmethod
    def _entry_to_json(e: Entry) -> dict[str, Any]:
        return {
            "path": e.path,
            "content_hash": e.content_hash,
            "size": e.size,
            "inline": base64.b64encode(e.inline).decode() if e.inline is not None else None,
        }

    @staticmethod
    def _entry_from_json(r: Mapping[str, Any]) -> Entry:
        inline = base64.b64decode(r["inline"]) if r["inline"] is not None else None
        return Entry(path=r["path"], content_hash=r["content_hash"], size=r["size"], inline=inline)

    # --- ref resolution ---

    def _pointer_scan(
        self, coord: Coordinate, name: str, *, refresh: bool = False
    ) -> tuple[int, Version | None]:
        """(raw tail seq, effective version) for a pointer, skipping tombstoned targets.

        The raw seq drives the gap-free CAS; the effective version is the newest event
        whose version is not tombstoned — so a pointer whose tail target was GC-dropped
        reverts to the last valid version rather than resolving to nothing. This upholds
        the S3Drop invariant (a reader never resolves to a reclaimed manifest), verified
        in ``model/S3Drop.tla``.
        """
        prefix = self._pointer_prefix(coord, name)
        keys = sorted(
            (k for k in self._list_names(prefix, refresh=refresh) if k.endswith(".json")),
            reverse=True,  # newest first
        )
        raw_seq = int(keys[0][: -len(".json")]) if keys else 0
        for key in keys:
            event = self._get_json(f"{prefix}/{key}")
            if event is not None and not self._is_tombstoned(event["version"]):
                return raw_seq, event["version"]
        return raw_seq, None

    def _resolve_ref(self, coord: Coordinate, ref: Ref) -> Version:
        if isinstance(ref, Pin):
            if self._is_tombstoned(ref.version) or ref.version not in self.list_versions(coord):
                raise NotFound(f"version {ref.version} not known for {coord}")
            return ref.version
        name = pointer_name(ref)
        _seq, effective = self._pointer_scan(coord, name)
        if effective is None:
            raise NotFound(f"pointer {name!r} not set for {coord}")
        return effective

    # --- Registry port: reads ---

    def head(self, coord: Coordinate, ref: Ref = HEAD) -> Version:
        return self._resolve_ref(coord, ref)

    def resolve(self, coord: Coordinate, ref: Ref = HEAD) -> Snapshot:
        version = self._resolve_ref(coord, ref)
        manifest = self._get_json(self._manifest_path(version))
        if manifest is None:
            raise NotFound(f"manifest {version} missing for {coord}")
        entries = tuple(self._entry_from_json(r) for r in manifest["entries"])
        return Snapshot(
            coord=coord,
            version=version,
            created_at=datetime.fromisoformat(manifest["created_at"]),
            metadata=manifest["metadata"],
            entries=entries,
        )

    def list_pointers(self, coord: Coordinate) -> Mapping[str, Version]:
        out: dict[str, Version] = {}
        for name in self._list_names(f"{self._coord_root(coord)}/pointers"):
            _seq, effective = self._pointer_scan(coord, name)  # skips tombstoned targets
            if effective is not None:
                out[name] = effective
        return out

    def list_versions(self, coord: Coordinate) -> Sequence[Version]:
        tombstoned = self._tombstoned_versions()  # one listing, not an fs.exists per event
        seen: dict[Version, None] = {}
        for _name, _seq, event in self._all_events(coord):
            if event["version"] not in tombstoned:
                seen.setdefault(event["version"], None)
        return list(seen)

    def list_coordinates(self) -> Sequence[Coordinate]:
        out: list[Coordinate] = []
        for name in self._list_names(f"{self.root}/coords"):
            for env in self._list_names(f"{self.root}/coords/{name}"):
                out.append(Coordinate(name, env))
        return out

    def list_log(self, coord: Coordinate) -> Sequence[LogEntry]:
        # History is forever: tip events for dropped versions are retained (not filtered).
        return [
            LogEntry(
                version=event["version"],
                seq=i,
                created_at=datetime.fromisoformat(event["at"]),
                actor=event.get("actor", "unknown"),
                reason=event.get("reason"),
            )
            for i, (_name, _seq, event) in enumerate(self._all_events(coord))
        ]

    def list_pointer_history(self, coord: Coordinate) -> Sequence[PointerMove]:
        return [
            PointerMove(
                name=name,
                from_version=event.get("from"),
                to_version=event["version"],
                actor=event.get("actor", "unknown"),
                reason=event.get("reason"),
                at=datetime.fromisoformat(event["at"]),
            )
            for name, _seq, event in self._all_events(coord)
        ]

    # --- Registry port: writes ---

    def commit(
        self, coord: Coordinate, entries: Iterable[Entry], metadata: Mapping[str, Any]
    ) -> Version:
        materialized = tuple(entries)
        version = manifest_version(materialized, self._hasher)
        body = json.dumps(
            {
                "entries": [self._entry_to_json(e) for e in materialized],
                "metadata": dict(metadata),
                "created_at": datetime.now(UTC).isoformat(),
            }
        ).encode()
        self._put_if_absent(self._manifest_path(version), body)  # False ⇒ idempotent no-op
        return version

    def set_pointer(
        self,
        coord: Coordinate,
        name: str,
        version: Version,
        *,
        expected: Version | None,
        actor: str = "unknown",
        reason: str | None = None,
    ) -> None:
        # Version validity is invariant across retries — check it once, before the loop
        # (and NotFound before Conflict, matching the reference/SQL backends' order).
        if self._is_tombstoned(version) or not self._exists(self._manifest_path(version)):
            raise NotFound(f"cannot point at uncommitted or reclaimed version {version}")
        while True:  # CAS loop: put-if-absent on the next sequence number
            seq, current = self._pointer_scan(coord, name, refresh=True)  # authoritative
            if current != expected:
                raise Conflict(f"pointer {name!r} for {coord} is {current}, expected {expected}")
            event = json.dumps(
                {
                    "seq": seq + 1,
                    "version": version,
                    "from": current,
                    "actor": actor,
                    "reason": reason,
                    "at": datetime.now(UTC).isoformat(),
                }
            ).encode()
            if self._put_if_absent(self._event_path(coord, name, seq + 1), event):
                return
            # FileExistsError: someone else took seq+1 → re-read tail and retry

    def drop_version(self, version: Version) -> None:
        """Reclaim an out-of-retention version. Safe against a concurrent promote.

        The order is load-bearing (verified in ``model/S3Drop.tla``): refuse if a pointer
        currently targets ``version``, then write the tombstone **before** deleting the
        manifest. A promote whose event lands in the race is made harmless because reads
        skip tombstoned targets (revert to the last valid version) — never resolving to
        the reclaimed manifest. Events are never pruned (history is forever).
        """
        # the target check must see live pointers, not a cached listing (scope to coords)
        self.fs.invalidate_cache(f"{self.root}/coords")
        for coord in self.list_coordinates():
            if version in self.list_pointers(coord).values():
                raise Conflict(f"cannot drop {version}: still a pointer target of {coord}")
        self._put_if_absent(self._tombstone_path(version), b"")  # tombstone BEFORE delete
        path = self._manifest_path(version)
        if self._exists(path):
            self.fs.rm(path)

    # --- lease surface: degenerate (no registry clock on S3) ---
    # Publish/GC blob-window safety comes from the blob-grace backstop, not leases.

    def acquire_lease(
        self, version: Version, hashes: Set[Hash], ttl: float = DEFAULT_LEASE_TTL
    ) -> LeaseId:
        return LeaseId(0)

    def renew_lease(self, lease_id: LeaseId, ttl: float = DEFAULT_LEASE_TTL) -> bool:
        return True

    def release_lease(self, lease_id: LeaseId) -> None:
        pass

    def active_leased_hashes(self) -> set[Hash]:
        return set()

    def active_leased_versions(self) -> set[Version]:
        return set()

    # --- helpers ---

    def _all_events(
        self, coord: Coordinate
    ) -> list[tuple[str, int, dict[str, Any]]]:
        """Every pointer-move event of a coordinate, merged across pointers by (at, name, seq)."""
        events: list[tuple[str, int, dict[str, Any]]] = []
        for name in self._list_names(f"{self._coord_root(coord)}/pointers"):
            prefix = self._pointer_prefix(coord, name)
            for key in self._list_names(prefix):
                if key.endswith(".json"):
                    event = self._get_json(f"{prefix}/{key}")
                    if event is not None:
                        events.append((name, event["seq"], event))
        events.sort(key=lambda t: (t[2]["at"], t[0], t[1]))
        return events

    def _tombstoned_versions(self) -> set[Version]:
        """Every dropped version, from one listing of the tombstone namespace (per algo)."""
        return {
            f"{algo}:{digest}"
            for algo in self._list_names(f"{self.root}/tombstones")
            for digest in self._list_names(f"{self.root}/tombstones/{algo}")
        }

    def _is_tombstoned(self, version: Version) -> bool:
        return self._exists(self._tombstone_path(version))


def _probe_conditional_writes(fs: AbstractFileSystem, root: str) -> None:
    """Fail fast if the endpoint does not enforce put-if-absent (S3-compatible stores vary)."""
    key = f"{root.rstrip('/')}/.probe/{uuid.uuid4().hex}"
    try:
        with cast("Any", fs.open(key, "xb")) as handle:
            handle.write(b"1")
        try:
            with cast("Any", fs.open(key, "xb")) as handle:  # must collide
                handle.write(b"2")
        except FileExistsError:
            return  # enforced — good
        raise SartreError(
            f"the object-store endpoint for {root!r} does not enforce put-if-absent "
            "(If-None-Match); S3Registry requires conditional-write support"
        )
    finally:
        try:
            fs.rm(key)
        except FileNotFoundError:
            pass


def open_s3(
    url: str,
    *,
    blob_url: str | None = None,
    storage_options: Mapping[str, Any] | None = None,
    cache_dir: str | None = None,
) -> Repository:
    """Open a shared repository backed entirely by object storage.

    ``url`` is an fsspec URL (``s3://bucket/repo``); the manifest plane lives directly
    under it and blobs default to ``<url>/blobs`` (override with ``blob_url``). One
    ``storage_options`` mapping configures both planes. ``cache_dir`` fronts the blob
    plane with a local read-through cache. Conditional-write support is verified lazily on
    the first write (so read-only credentials can run read-only commands); the first
    ``commit``/``set_pointer`` raises if the endpoint does not enforce put-if-absent.
    """
    import fsspec

    from sartre.repository import Repository
    from sartre.store import CachingStore, CasStore, FsspecBlobBackend

    opts = dict(storage_options or {})
    fs, root = fsspec.core.url_to_fs(url, **opts)
    registry = S3Registry(fs, root)  # conditional-write support is verified lazily on first write

    if blob_url is None:
        blob_fs, blob_root = fs, f"{root.rstrip('/')}/blobs"
    else:
        blob_fs, blob_root = fsspec.core.url_to_fs(blob_url, **opts)
    store: Any = CasStore(FsspecBlobBackend(blob_fs, blob_root))
    if cache_dir is not None:
        local = CasStore(FsspecBlobBackend(fsspec.filesystem("file"), cache_dir))
        store = CachingStore(local=local, remote=store)
    return Repository(registry, store)
