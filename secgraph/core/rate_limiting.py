"""
Thread-safe rate limiting utility for external API calls.

This module provides a unified rate limiting implementation for external APIs
(SEC EDGAR, yfinance).

NOTE: This is NOT for datamule - datamule handles its own rate limiting
via the requests_per_second parameter in download_submissions().

Usage:
    from secgraph.utils.rate_limiting import RateLimiter

    # Create a rate limiter (10 requests per second)
    limiter = RateLimiter(requests_per_second=10.0, source_name="yfinance")

    # Use as a callable
    limiter()
    make_api_call()

    # Or use as a context manager
    with limiter:
        make_api_call()
"""

import time
from threading import Lock


class RateLimiter:
    """
    Thread-safe rate limiter for external API calls.

    Enforces a minimum interval between calls to prevent exceeding rate limits.
    Thread-safe for use in concurrent environments.

    Args:
        requests_per_second: Maximum requests per second allowed
        source_name: Name of the source (for logging/debugging)

    Example:
        >>> limiter = RateLimiter(requests_per_second=10.0, source_name="api")
        >>> limiter()  # First call - no wait
        >>> limiter()  # Second call - waits if needed to maintain 10 req/sec
    """

    def __init__(self, requests_per_second: float, source_name: str = "default"):
        """
        Initialize rate limiter.

        Args:
            requests_per_second: Maximum requests per second (must be > 0)
            source_name: Name of the source (for debugging)

        Raises:
            ValueError: If requests_per_second <= 0
        """
        if requests_per_second <= 0:
            raise ValueError(f"requests_per_second must be > 0, got {requests_per_second}")

        self.requests_per_second = requests_per_second
        self.source_name = source_name
        self.min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._lock = Lock()
        self._last_call = 0.0

    def __call__(self) -> None:
        """
        Enforce rate limiting by waiting if necessary.

        This method is thread-safe and can be called from multiple threads.
        It will sleep if the minimum interval has not elapsed since the last call.
        """
        with self._lock:
            current_time = time.time()
            elapsed = current_time - self._last_call

            if elapsed < self.min_interval:
                sleep_time = self.min_interval - elapsed
                time.sleep(sleep_time)

            self._last_call = time.time()

    def __enter__(self):
        """Context manager entry - enforces rate limiting."""
        self()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - no cleanup needed."""
        return False

    def reset(self) -> None:
        """
        Reset the rate limiter (clears last call time).

        Useful for testing or when you want to allow immediate calls.
        """
        with self._lock:
            self._last_call = 0.0


# Global rate limiters for common sources
# These can be imported and used directly, or create new instances as needed
_rate_limiters: dict[str, RateLimiter] = {}
_rate_limiters_lock = Lock()


def get_rate_limiter(
    source_name: str,
    requests_per_second: float,
    create_if_missing: bool = True,
) -> RateLimiter | None:
    """
    Get or create a global rate limiter for a source.

    This function provides a convenient way to get shared rate limiters
    across different modules without passing them around.

    Args:
        source_name: Name of the source (e.g., "yfinance", "sec_edgar")
        requests_per_second: Maximum requests per second
        create_if_missing: If True, create a new limiter if one doesn't exist

    Returns:
        RateLimiter instance, or None if create_if_missing is False and limiter doesn't exist

    Example:
        >>> limiter = get_rate_limiter("yfinance", requests_per_second=10.0)
        >>> limiter()
        >>> make_api_call()
    """
    with _rate_limiters_lock:
        if source_name in _rate_limiters:
            return _rate_limiters[source_name]

        if not create_if_missing:
            return None

        limiter = RateLimiter(requests_per_second=requests_per_second, source_name=source_name)
        _rate_limiters[source_name] = limiter
        return limiter
