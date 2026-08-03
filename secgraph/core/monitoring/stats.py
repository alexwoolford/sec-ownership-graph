"""
Thread-safe statistics tracking for parallel execution.

Provides a simple, reusable pattern for tracking statistics across multiple threads.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock


@dataclass
class ExecutionStats:
    """
    Thread-safe statistics tracker for parallel execution.

    Example:
        stats = ExecutionStats(success=0, failed=0, cached=0)
        with stats.lock:
            stats.success += 1
        # Or use increment method
        stats.increment("success")
    """

    def __init__(self, **initial_values: int):
        """
        Initialize stats with any number of counters.

        Args:
            **initial_values: Initial values for stat counters (default: 0)

        Example:
            stats = ExecutionStats(success=0, failed=0, cached=0)
        """
        self._lock = Lock()
        self._counters: dict[str, int] = dict(initial_values)

    @property
    def lock(self) -> Lock:
        """Get the thread lock for manual synchronization."""
        return self._lock

    def increment(self, key: str, amount: int = 1, lock: Lock | None = None) -> None:
        """
        Thread-safe increment of a counter.

        Args:
            key: Counter name
            amount: Amount to increment (default: 1)
            lock: Optional lock to use (default: uses internal lock)

        Example:
            stats.increment("success")
            stats.increment("failed", amount=2)
        """
        lock_to_use = lock if lock is not None else self._lock
        with lock_to_use:
            self._counters[key] = self._counters.get(key, 0) + amount

    def get(self, key: str, default: int = 0) -> int:
        """Get counter value."""
        return self._counters.get(key, default)

    def set(self, key: str, value: int) -> None:
        """Set counter value (not thread-safe - use with lock)."""
        self._counters[key] = value

    def to_dict(self) -> dict[str, int]:
        """Get all counters as a dictionary."""
        with self._lock:
            return self._counters.copy()

    def __getitem__(self, key: str) -> int:
        """Allow dict-like access: stats['success']."""
        return self._counters.get(key, 0)

    def __setitem__(self, key: str, value: int) -> None:
        """Allow dict-like assignment: stats['success'] = 5 (not thread-safe)."""
        self._counters[key] = value

    def __repr__(self) -> str:
        """String representation."""
        with self._lock:
            items = ", ".join(f"{k}={v}" for k, v in sorted(self._counters.items()))
            return f"ExecutionStats({items})"
