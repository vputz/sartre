"""Content-hash identity: self-describing ``"<algo>:<digest>"`` keys.

Keys carry their own algorithm so that blobs hashed under different algorithms
coexist in one manifest without rewrites (design decision D3). ``sha256`` is the
default (stdlib, FIPS-approved, no native dependency); ``blake3`` can be added
later as an alternative ``Hasher`` without changing existing keys.
"""

from __future__ import annotations

import hashlib
from typing import BinaryIO, Protocol, runtime_checkable

_CHUNK = 1 << 20  # 1 MiB streaming reads — never buffer a whole blob


def make_key(algorithm: str, digest: str) -> str:
    """Compose a self-describing content key from an algorithm and hex digest."""
    return f"{algorithm}:{digest}"


def parse_key(key: str) -> tuple[str, str]:
    """Split a content key into ``(algorithm, digest)``; raise ``ValueError`` if malformed."""
    algorithm, sep, digest = key.partition(":")
    if not sep or not algorithm or not digest:
        raise ValueError(f"not a self-describing content hash: {key!r}")
    return algorithm, digest


def algorithm_of(key: str) -> str:
    """Return the algorithm component of a content key."""
    return parse_key(key)[0]


@runtime_checkable
class Hasher(Protocol):
    """Hashes a byte stream into a self-describing content key.

    Implementations MUST read ``data`` incrementally so arbitrarily large blobs
    never need to be buffered whole.
    """

    algorithm: str

    def hash(self, data: BinaryIO) -> str:
        """Return the ``"<algorithm>:<digest>"`` key for the bytes of ``data``."""
        ...


class Sha256Hasher:
    """Default :class:`Hasher` using ``hashlib.sha256`` with streaming reads."""

    algorithm = "sha256"

    def hash(self, data: BinaryIO) -> str:
        digest = hashlib.sha256()
        while chunk := data.read(_CHUNK):
            digest.update(chunk)
        return make_key(self.algorithm, digest.hexdigest())


DEFAULT_HASHER: Hasher = Sha256Hasher()
