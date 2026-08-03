"""
Parallel execution utilities for secgraph scripts.

Provides reusable patterns for parallel execution with progress tracking,
error handling, and statistics collection.
"""

import logging
import sys
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import (
    Any,
    TypeVar,
)

from tqdm import tqdm

from secgraph.core.monitoring.stats import ExecutionStats

logger = logging.getLogger(__name__)

T = TypeVar("T")  # Input type
R = TypeVar("R")  # Result type


def execute_parallel(
    items: Iterable[T],
    worker_func: Callable[[T], R],
    max_workers: int = 8,
    desc: str = "Processing",
    unit: str = "item",
    show_progress: bool = True,
    error_handler: Callable[[T, Exception], None] | None = None,
    result_handler: Callable[[T, R], None] | None = None,
    stats: ExecutionStats | None = None,
    stats_key: str | None = None,
    timeout: float | None = None,
    progress_postfix: Callable[[], dict[str, Any]] | None = None,
) -> list[tuple[T, R | None, Exception | None]]:
    """
    Execute a function in parallel across multiple items with progress tracking.

    Args:
        items: Iterable of items to process
        worker_func: Function to call for each item (takes item, returns result)
        max_workers: Maximum number of parallel workers
        desc: Progress bar description
        unit: Progress bar unit name
        show_progress: Whether to show progress bar
        error_handler: Optional callback for errors (item, exception) -> None
        result_handler: Optional callback for results (item, result) -> None
        stats: Optional ExecutionStats instance for tracking
        stats_key: Optional key to increment in stats on success
        timeout: Optional timeout per task in seconds

    Returns:
        List of tuples: (item, result, exception) for each item processed

    Example:
        def process_file(file_path: Path) -> Dict:
            # Process file
            return {"status": "ok"}

        results = execute_parallel(
            file_paths,
            process_file,
            max_workers=8,
            desc="Processing files",
            unit="file"
        )

        for item, result, error in results:
            if error:
                print(f"Error processing {item}: {error}")
            else:
                print(f"Success: {result}")
    """
    items_list = list(items)  # Convert to list to get length
    total = len(items_list)

    if total == 0:
        return []

    results: list[tuple[T, R | None, Exception | None]] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_item = {executor.submit(worker_func, item): item for item in items_list}

        # Process completed tasks
        progress_bar = None
        if show_progress:
            progress_bar = tqdm(
                total=total,
                desc=desc,
                unit=unit,
                file=sys.stderr,  # Use stderr to avoid conflicts
                ncols=100,
                mininterval=1.0,  # Update at most once per second
                miniters=50,  # Reduce terminal spam
                dynamic_ncols=True,
            )

        try:
            for future in as_completed(future_to_item, timeout=timeout):
                item = future_to_item[future]
                result = None
                error = None

                try:
                    if timeout:
                        result = future.result(timeout=timeout)
                    else:
                        result = future.result()

                    # Call result handler if provided
                    if result_handler:
                        result_handler(item, result)

                    # Update stats if provided
                    if stats and stats_key:
                        stats.increment(stats_key)

                except Exception as e:
                    error = e

                    # Call error handler if provided
                    if error_handler:
                        error_handler(item, e)
                    else:
                        # Default: log error
                        logger.debug(f"Error processing {item}: {e}")

                    # Update stats if provided
                    if stats:
                        stats.increment("failed")

                finally:
                    results.append((item, result, error))
                    if progress_bar:
                        # Update postfix if provided
                        if progress_postfix:
                            postfix = progress_postfix()
                            progress_bar.set_postfix(postfix)
                        progress_bar.update(1)

        finally:
            if progress_bar:
                progress_bar.close()

    return results


def execute_parallel_with_stats(
    items: Iterable[T],
    worker_func: Callable[[T, ExecutionStats], R],
    max_workers: int = 8,
    desc: str = "Processing",
    unit: str = "item",
    show_progress: bool = True,
    progress_postfix: Callable[[ExecutionStats], dict[str, Any]] | None = None,
    log_interval: int | None = None,
    logger_instance: logging.Logger | None = None,
) -> tuple[list[tuple[T, R | None, Exception | None]], ExecutionStats]:
    """
    Execute in parallel with integrated stats tracking and progress updates.

    This variant passes the stats object to the worker function, allowing it
    to update stats directly. The progress bar is automatically updated with
    current stats.

    Args:
        items: Iterable of items to process
        worker_func: Function that takes (item, stats) and returns result
        max_workers: Maximum number of parallel workers
        desc: Progress bar description
        unit: Progress bar unit name
        show_progress: Whether to show progress bar
        progress_postfix: Optional function to generate progress bar postfix from stats
        log_interval: Optional interval for logging progress (e.g., every 100 items)
        logger_instance: Optional logger for progress logging

    Returns:
        Tuple of (results list, stats object)

    Example:
        def process_file(file_path: Path, stats: ExecutionStats) -> Dict:
            try:
                # Process file
                stats.increment("success")
                return {"status": "ok"}
            except Exception:
                stats.increment("failed")
                raise

        results, stats = execute_parallel_with_stats(
            file_paths,
            process_file,
            max_workers=8,
            desc="Processing files",
            progress_postfix=lambda s: {
                "success": s.get("success"),
                "failed": s.get("failed")
            }
        )
    """
    items_list = list(items)
    total = len(items_list)

    if total == 0:
        return [], ExecutionStats()

    stats = ExecutionStats()
    results: list[tuple[T, R | None, Exception | None]] = []
    log_logger = logger_instance or logger

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks (pass stats to worker)
        # Wrap worker_func to install thread-safe output capture in each worker thread
        def wrapped_worker_func(item, worker_stats):
            # Install thread-safe output capture for this worker thread
            # This captures stdout/stderr from external libraries (like datamule)
            # that print from worker threads
            try:
                from secgraph.core.concurrency.thread_safe_output import (
                    install_thread_output_capture,
                )

                install_thread_output_capture()
            except Exception:
                pass  # Continue even if output capture fails

            try:
                return worker_func(item, worker_stats)
            finally:
                # Cleanup is handled in the result processing loop
                pass

        future_to_item = {
            executor.submit(wrapped_worker_func, item, stats): item for item in items_list
        }

        # Process completed tasks
        progress_bar = None
        if show_progress:
            # Configure tqdm for clean terminal output
            progress_bar = tqdm(
                total=total,
                desc=desc,
                unit=unit,
                file=sys.stderr,  # Use stderr to avoid conflicts with stdout
                ncols=100,
                disable=False,
                mininterval=1.0,  # Update at most once per second
                miniters=50,  # Update at most every 50 items (reduces terminal spam)
                smoothing=0.3,
                dynamic_ncols=True,  # Adapt to terminal width
            )

        import time

        _start_time = time.perf_counter()

        # Log startup message to file only (DEBUG level)
        # Don't log to console - scripts handle their own console output before calling parallel
        if log_logger:
            startup_msg = f"Starting processing of {total:,} items with {max_workers} workers"
            log_logger.debug(startup_msg)

        try:
            for future in as_completed(future_to_item):
                item = future_to_item[future]
                result = None
                error = None

                try:
                    result = future.result()
                except Exception as e:
                    error = e
                    logger.debug(f"Error processing {item}: {e}")
                finally:
                    # Clean up any thread-local output capture from worker thread
                    # This ensures datamule output doesn't leak to stdout
                    # Note: Cleanup happens in the worker thread itself via wrapped_worker_func
                    # This is just a safety net in case worker doesn't clean up
                    try:
                        from secgraph.core.concurrency.thread_safe_output import (
                            uninstall_thread_output_capture,
                        )

                        # Uninstall returns captured output (we can log it if needed)
                        stdout, stderr = uninstall_thread_output_capture()
                        # Filter and log meaningful content (similar to datamule suppression)
                        if stdout or stderr:
                            # Output is already captured, just ensure cleanup
                            pass
                    except Exception:
                        pass  # Ignore errors in cleanup

                results.append((item, result, error))
                completed = len(results)

                # Update progress bar
                if progress_bar:
                    # Update postfix with current stats
                    if progress_postfix:
                        postfix = progress_postfix(stats)
                        progress_bar.set_postfix(postfix)

                    progress_bar.update(1)
                    progress_bar.refresh()  # Force immediate refresh

                # Periodic logging - log to file only, tqdm progress bar handles display
                # This avoids interference between logging and tqdm progress bars
                if log_logger:
                    # Log every 100 files (or log_interval if specified)
                    # Only log to file, not to console (tqdm handles console display)
                    log_every = log_interval or 100
                    if completed > 0 and completed % log_every == 0:
                        stats_dict = stats.to_dict()
                        stats_str = " | ".join(f"{k}: {v:,}" for k, v in sorted(stats_dict.items()))
                        # Calculate percentage and rate
                        percentage = (completed / total * 100) if total > 0 else 0

                        # Calculate rate (items per second)
                        import time

                        elapsed = time.perf_counter() - _start_time
                        rate = completed / elapsed if elapsed > 0 else 0
                        eta_seconds = (total - completed) / rate if rate > 0 else 0
                        eta_minutes = eta_seconds / 60

                        message = f"Progress: {completed:,}/{total:,} ({percentage:.1f}%) | {rate:.2f} files/sec | ETA: {eta_minutes:.1f} min | {stats_str}"

                        # Log progress at INFO level (needed for tests and user visibility)
                        # When tqdm is active, TqdmLoggingHandler routes this through tqdm.write()
                        log_logger.info(message)

        finally:
            if progress_bar:
                progress_bar.close()

    return results, stats
