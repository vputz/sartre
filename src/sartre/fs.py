"""``SnapshotFS``: a read-only fsspec filesystem over a resolved version.

Listings (``ls``/``info``/``exists``) are served entirely from the manifest with
no blob fetch — directories are *synthetic*, derived from the path prefixes of the
entries. ``_open`` maps a logical path to its content hash and reads through the
``Store``, returning a seekable, integrity-verified handle (so random access — a
parquet-footer seek — is a seek on a materialized blob). All mutation is rejected.

``sartre://`` URL registration, FUSE mounting, and the key/value mapper are future
work; addressing here is object-form — obtain the filesystem from a snapshot (e.g.
``repo.snapshot_fs(snap)``) and hand it to any fsspec-aware consumer.
"""

from __future__ import annotations

from typing import Any, cast

from fsspec import AbstractFileSystem

from sartre.model import Entry, Snapshot
from sartre.ports import Store

_READ_ONLY = "SnapshotFS is read-only"


def _parent(path: str) -> str:
    """The canonical parent directory of a path (``""`` for a top-level entry)."""
    return path.rsplit("/", 1)[0] if "/" in path else ""


class SnapshotFS(AbstractFileSystem):
    """A read-only :class:`~fsspec.AbstractFileSystem` bound to a snapshot + store."""

    protocol = "sartre"
    # Distinct snapshots/stores must not share a cached instance; disable fsspec's
    # instance cache so each binding is its own filesystem.
    cachable = False

    def __init__(self, snapshot: Snapshot, store: Store, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.snapshot = snapshot
        self.store = store
        self._files: dict[str, Entry] = {e.path: e for e in snapshot.entries}
        self._dirs: set[str] = set()
        for path in self._files:
            parent = _parent(path)
            while parent:  # register every intermediate prefix as a directory
                self._dirs.add(parent)
                parent = _parent(parent)

    @classmethod
    def _strip_protocol(cls, path: str) -> str:
        """Reduce a path to the canonical, root-relative key used by the index."""
        stripped = cast(str, super()._strip_protocol(path))
        return stripped.strip("/")

    def _file_info(self, path: str) -> dict[str, Any]:
        return {"name": path, "size": self._files[path].size, "type": "file"}

    def _dir_info(self, path: str) -> dict[str, Any]:
        return {"name": path, "size": 0, "type": "directory"}

    def info(self, path: str, **kwargs: Any) -> dict[str, Any]:
        path = self._strip_protocol(path)
        if path == "":
            return self._dir_info("")
        if path in self._files:
            return self._file_info(path)
        if path in self._dirs:
            return self._dir_info(path)
        raise FileNotFoundError(path)

    def ls(self, path: str, detail: bool = True, **kwargs: Any) -> Any:
        path = self._strip_protocol(path)
        if path != "" and path not in self._dirs:
            if path in self._files:  # ls of a file lists just that file
                return [self._file_info(path)] if detail else [path]
            raise FileNotFoundError(path)
        children: list[dict[str, Any]] = [
            self._file_info(p) for p in self._files if _parent(p) == path
        ]
        children += [self._dir_info(d) for d in self._dirs if _parent(d) == path]
        children.sort(key=lambda info: info["name"])
        return children if detail else [info["name"] for info in children]

    def exists(self, path: str, **kwargs: Any) -> bool:
        path = self._strip_protocol(path)
        return path == "" or path in self._files or path in self._dirs

    def _open(
        self,
        path: str,
        mode: str = "rb",
        block_size: int | None = None,
        autocommit: bool = True,
        cache_options: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:  # fsspec expects AbstractBufferedFile; the CAS handle is a seekable BinaryIO
        if mode != "rb":
            raise PermissionError(_READ_ONLY)
        path = self._strip_protocol(path)
        entry = self._files.get(path)
        if entry is None:
            if path in self._dirs:
                raise IsADirectoryError(path)
            raise FileNotFoundError(path)
        # The CAS store returns a seekable, integrity-verified handle; return it
        # directly rather than layering fsspec's block-cache — the CAS is the cache.
        return self.store.open(entry.content_hash)

    def _rm(self, path: str) -> None:
        raise PermissionError(_READ_ONLY)

    def mkdir(self, path: str, create_parents: bool = True, **kwargs: Any) -> None:
        raise PermissionError(_READ_ONLY)
