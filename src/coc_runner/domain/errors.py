from __future__ import annotations


class ConflictError(RuntimeError):
    """Raised when optimistic locking or stale state checks fail."""

