"""sartre — Simple ARTifact REpository.

A content-addressed, versioned binary artifact repository. Two planes: a manifest
plane (``Registry``: names → versions → manifests) and a blob plane (``Store``:
content-hash ↔ bytes), composed by a ``Repository``. See
``binary-artifact-repo-design.md``.
"""

from __future__ import annotations

from sartre.errors import (
    Conflict,
    IntegrityError,
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
    make_key,
    manifest_version,
    parse_key,
)
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
from sartre.ports import BlobBackend, Registry, Store
from sartre.repository import AsyncRepository, Repository
from sartre.store import CachingStore, CasStore, FsspecBlobBackend

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
    "Hash",
    "Hasher",
    "Head",
    "IntegrityError",
    "NotFound",
    "PathError",
    "Pin",
    "Ref",
    "Registry",
    "Repository",
    "SartreError",
    "Sha256Hasher",
    "Snapshot",
    "SnapshotFS",
    "Store",
    "Version",
    "algorithm_of",
    "check_no_case_collisions",
    "make_key",
    "manifest_version",
    "normalize_path",
    "parse_key",
]
