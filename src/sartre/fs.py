"""``SnapshotFS``: a read-only fsspec filesystem over a resolved version.

Interface stub. Listings (``ls``/``info``) are served from the manifest with no
blob fetch; ``_open`` maps a logical path to its content hash and reads through
the ``Store``, returning a handle backed by the content-addressed cache (so
random access is a seek in a local file). All mutation is rejected. Method bodies
are deferred to a follow-up change.
"""

from __future__ import annotations

from typing import Any

from fsspec import AbstractFileSystem

from sartre.model import Snapshot
from sartre.ports import Store

_DEFERRED = "implementation deferred to a follow-up change (add-core-ports is interface-only)"
_READ_ONLY = "SnapshotFS is read-only"


class SnapshotFS(AbstractFileSystem):
    """A read-only :class:`~fsspec.AbstractFileSystem` bound to a snapshot + store."""

    protocol = "sartre"

    def __init__(self, snapshot: Snapshot, store: Store, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.snapshot = snapshot
        self.store = store

    def ls(self, path: str, detail: bool = True, **kwargs: Any) -> Any:
        # Served from snapshot.entries — no blob fetch.
        raise NotImplementedError(_DEFERRED)

    def info(self, path: str, **kwargs: Any) -> Any:
        # Served from snapshot.entries — no blob fetch.
        raise NotImplementedError(_DEFERRED)

    def _open(
        self,
        path: str,
        mode: str = "rb",
        block_size: int | None = None,
        autocommit: bool = True,
        cache_options: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        if mode != "rb":
            raise PermissionError(_READ_ONLY)
        raise NotImplementedError(_DEFERRED)

    def _rm(self, path: str) -> None:
        raise PermissionError(_READ_ONLY)

    def mkdir(self, path: str, create_parents: bool = True, **kwargs: Any) -> None:
        raise PermissionError(_READ_ONLY)
