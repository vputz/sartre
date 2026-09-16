"""sartre — Simple ARTifact REpository.

A content-addressed, versioned binary artifact repository. Two planes: a manifest
plane (``Registry``: names → versions → manifests) and a blob plane (``Store``:
content-hash ↔ bytes), composed by a ``Repository``. See
``binary-artifact-repo-design.md``.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

from sartre.cloud import open_cloud
from sartre.errors import (
    Conflict,
    IntegrityError,
    LeaseExpired,
    NotFound,
    PathError,
    SartreError,
)
from sartre.fs import SnapshotFS
from sartre.hashing import (
    DEFAULT_HASHER,
    Hasher,
    Sha256Hasher,
    algorithm_of,
    hasher_for,
    make_key,
    manifest_version,
    parse_key,
)
from sartre.local import open_local
from sartre.memory import MemoryRegistry
from sartre.model import (
    HEAD,
    Alias,
    Coordinate,
    Entry,
    Hash,
    Head,
    Pin,
    Ref,
    Snapshot,
    Version,
)
from sartre.paths import check_no_case_collisions, normalize_path
from sartre.ports import BlobBackend, LeaseId, LogEntry, PointerMove, Registry, Store
from sartre.postgres import PostgresRegistry
from sartre.repository import AsyncRepository, GCResult, Repository, RetentionPolicy
from sartre.s3 import S3Registry, open_s3
from sartre.sqlite import SqliteRegistry
from sartre.store import CachingStore, CasStore, FsspecBlobBackend

try:  # the build's declared version is the single source of truth (pyproject `version`)
    __version__ = _version("sartre")
except PackageNotFoundError:  # running from a raw checkout without installed metadata
    __version__ = "0.0.0+unknown"

__all__ = [
    "DEFAULT_HASHER",
    "HEAD",
    "Alias",
    "AsyncRepository",
    "BlobBackend",
    "CachingStore",
    "CasStore",
    "Conflict",
    "Coordinate",
    "Entry",
    "FsspecBlobBackend",
    "GCResult",
    "Hash",
    "Hasher",
    "Head",
    "IntegrityError",
    "LeaseExpired",
    "LeaseId",
    "LogEntry",
    "MemoryRegistry",
    "NotFound",
    "PathError",
    "Pin",
    "PointerMove",
    "PostgresRegistry",
    "Ref",
    "Registry",
    "Repository",
    "RetentionPolicy",
    "SartreError",
    "Sha256Hasher",
    "Snapshot",
    "SnapshotFS",
    "S3Registry",
    "SqliteRegistry",
    "open_s3",
    "Store",
    "Version",
    "algorithm_of",
    "check_no_case_collisions",
    "hasher_for",
    "make_key",
    "manifest_version",
    "normalize_path",
    "open_cloud",
    "open_local",
    "parse_key",
]
