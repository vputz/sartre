"""Typed errors for sartre.

Consumers branch on these rather than on string messages. ``resolve`` raising
``NotFound`` is the contract that lets a bootstrap/seed call fail hard (design
memo Pattern B); ``Conflict`` is raised by the compare-and-swap pointer update;
``IntegrityError`` is raised when fetched bytes do not match their content hash.
"""

from __future__ import annotations


class SartreError(Exception):
    """Base class for all sartre errors."""


class NotFound(SartreError):
    """A coordinate, ref, version, or blob does not exist."""


class Conflict(SartreError):
    """A compare-and-swap pointer update found an unexpected current value."""


class LeaseExpired(Conflict):
    """A publish's lease lapsed mid-flight (before commit or before advancing the
    pointer). The publish aborts rather than write over possibly-reclaimed blobs; it is
    a retryable condition, so it subclasses :class:`Conflict`."""


class IntegrityError(SartreError):
    """Fetched bytes did not hash to the requested content key."""


class PathError(SartreError, ValueError):
    """A logical path is not valid under the canonical path model."""
