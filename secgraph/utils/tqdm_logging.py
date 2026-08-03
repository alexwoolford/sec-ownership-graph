"""
Tqdm-compatible logging utilities.

This module provides utilities for clean logging during tqdm progress bars:
1. TqdmLoggingHandler - routes log messages through tqdm.write() to avoid interference
2. File descriptor level output capture - captures ALL stdout/stderr including C extensions
3. Context managers for suppressing output during parallel execution
"""

import contextlib
import io
import logging
import os
import sys
import tempfile
import threading
from typing import TextIO

from tqdm import tqdm


class TqdmLoggingHandler(logging.Handler):
    """
    Logging handler that writes through tqdm.write() to avoid progress bar interference.

    This ensures that log messages appear cleanly on separate lines, without
    being interleaved with or breaking up tqdm progress bars.

    Usage:
        handler = TqdmLoggingHandler(level=logging.INFO)
        logger.addHandler(handler)
    """

    def __init__(self, level: int = logging.NOTSET, stream: TextIO | None = None):
        """
        Initialize the handler.

        Args:
            level: Minimum logging level to handle
            stream: Output stream (default: sys.stderr for tqdm compatibility)
        """
        super().__init__(level)
        self.stream = stream or sys.stderr

    def emit(self, record: logging.LogRecord) -> None:
        """Emit a log record through tqdm.write()."""
        try:
            msg = self.format(record)
            # Use tqdm.write() to ensure clean output alongside progress bars
            # file=self.stream ensures it goes to the correct stream
            tqdm.write(msg, file=self.stream)
        except RecursionError:
            raise
        except Exception:
            self.handleError(record)


class FileDescriptorCapture:
    """
    Capture stdout/stderr at the file descriptor level.

    This is more robust than sys.stdout redirection because it captures:
    - print() statements from external libraries
    - Output from C extensions
    - Output from subprocess calls (to some extent)
    - Any write() to file descriptor 1 (stdout) or 2 (stderr)

    Usage:
        with FileDescriptorCapture(capture_stdout=True, capture_stderr=True) as capture:
            # Any output here will be captured
            some_noisy_library.do_stuff()

        stdout_content, stderr_content = capture.get_captured_output()
    """

    def __init__(
        self,
        capture_stdout: bool = True,
        capture_stderr: bool = True,
        log_file: TextIO | None = None,
    ):
        """
        Initialize the capture.

        Args:
            capture_stdout: Whether to capture stdout (fd 1)
            capture_stderr: Whether to capture stderr (fd 2)
            log_file: Optional file to write captured output to (for logging)
        """
        self.capture_stdout = capture_stdout
        self.capture_stderr = capture_stderr
        self.log_file = log_file
        self._lock = threading.Lock()

        # File descriptors and buffers
        self._stdout_fd: int | None = None
        self._stderr_fd: int | None = None
        self._stdout_backup: int | None = None
        self._stderr_backup: int | None = None
        self._stdout_temp: tempfile._TemporaryFileWrapper | None = None
        self._stderr_temp: tempfile._TemporaryFileWrapper | None = None

        # Captured content
        self._stdout_content = ""
        self._stderr_content = ""

    def __enter__(self) -> "FileDescriptorCapture":
        """Start capturing output."""
        with self._lock:
            if self.capture_stdout:
                # Save original fd 1
                self._stdout_fd = sys.stdout.fileno()
                self._stdout_backup = os.dup(self._stdout_fd)
                # Create temp file for capture
                self._stdout_temp = tempfile.NamedTemporaryFile(
                    mode="w+", delete=False, suffix=".stdout"
                )
                # Redirect fd 1 to temp file
                os.dup2(self._stdout_temp.fileno(), self._stdout_fd)

            if self.capture_stderr:
                # Save original fd 2
                self._stderr_fd = sys.stderr.fileno()
                self._stderr_backup = os.dup(self._stderr_fd)
                # Create temp file for capture
                self._stderr_temp = tempfile.NamedTemporaryFile(
                    mode="w+", delete=False, suffix=".stderr"
                )
                # Redirect fd 2 to temp file
                os.dup2(self._stderr_temp.fileno(), self._stderr_fd)

        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Stop capturing and restore original file descriptors."""
        with self._lock:
            if (
                self.capture_stdout
                and self._stdout_backup is not None
                and self._stdout_fd is not None
            ):
                # Flush and restore
                sys.stdout.flush()
                os.dup2(self._stdout_backup, self._stdout_fd)
                os.close(self._stdout_backup)
                # Read captured content
                if self._stdout_temp:
                    self._stdout_temp.flush()
                    self._stdout_temp.seek(0)
                    self._stdout_content = self._stdout_temp.read()
                    self._stdout_temp.close()
                    # Clean up temp file
                    try:
                        os.unlink(self._stdout_temp.name)
                    except Exception:
                        pass

            if (
                self.capture_stderr
                and self._stderr_backup is not None
                and self._stderr_fd is not None
            ):
                # Flush and restore
                sys.stderr.flush()
                os.dup2(self._stderr_backup, self._stderr_fd)
                os.close(self._stderr_backup)
                # Read captured content
                if self._stderr_temp:
                    self._stderr_temp.flush()
                    self._stderr_temp.seek(0)
                    self._stderr_content = self._stderr_temp.read()
                    self._stderr_temp.close()
                    # Clean up temp file
                    try:
                        os.unlink(self._stderr_temp.name)
                    except Exception:
                        pass

            # Write captured content to log file if provided
            if self.log_file and (self._stdout_content or self._stderr_content):
                self._write_to_log()
        # Don't suppress exceptions (returning None means False)

    def get_captured_output(self) -> tuple[str, str]:
        """Get captured stdout and stderr content."""
        return self._stdout_content, self._stderr_content

    def _write_to_log(self) -> None:
        """Write captured content to log file with filtering."""
        if self.log_file is None:
            return

        import datetime

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for line in (self._stdout_content + self._stderr_content).splitlines():
            line = line.strip()
            if not line:
                continue
            # Filter out known noise patterns
            if self._is_noise(line):
                continue
            # Write meaningful content to log
            self.log_file.write(f"[{timestamp}] [captured] {line}\n")
        self.log_file.flush()

    @staticmethod
    def _is_noise(line: str) -> bool:
        """Check if a line is noise that should be filtered out."""
        line_lower = line.lower()

        # Progress bar patterns
        if any(char in line for char in ["█", "▉", "▊", "▋", "▌", "▍", "▎", "▏", "░", "▒", "▓"]):
            return True

        # tqdm patterns
        if any(pattern in line for pattern in ["%|", "it/s]", "s/it]", "\x1b["]):
            return True

        # Datamule noise patterns
        noise_patterns = [
            "loading submissions",
            "loading regular submissions",
            "successfully loaded",
            "query complete",
            "retrieved 0 records",
            "total cost:",
            "remaining balance:",
            "time:",
            "no records found",
        ]
        if any(pattern in line_lower for pattern in noise_patterns):
            return True

        # Very short lines are usually fragments (< 10 chars)
        if len(line) < 10:
            return True

        return False


@contextlib.contextmanager
def suppress_all_output(log_file: TextIO | None = None):
    """
    Context manager to suppress ALL stdout/stderr output.

    Uses file descriptor level redirection to capture output from:
    - Python print() statements
    - External C libraries
    - Subprocess output

    Args:
        log_file: Optional file to write captured output to (filtered)

    Example:
        with suppress_all_output(log_file):
            noisy_library.do_something()  # Output captured, not shown
    """
    # Use FD-level capture for maximum compatibility
    with FileDescriptorCapture(
        capture_stdout=True, capture_stderr=True, log_file=log_file
    ) as capture:
        yield capture


@contextlib.contextmanager
def redirect_output_to_tqdm(logger_instance: logging.Logger | None = None):
    """
    Context manager that redirects stdout to go through tqdm.write().

    This prevents external libraries that print to stdout from breaking
    tqdm progress bars. stderr is left alone since tqdm uses it.

    Note: This uses Python-level redirection (sys.stdout), not FD-level.
    For capturing C extension output, use FileDescriptorCapture instead.

    Args:
        logger_instance: Optional logger to write captured output to

    Example:
        with redirect_output_to_tqdm(logger):
            # Progress bars work cleanly here
            for item in tqdm(items):
                external_library.process(item)  # prints won't break tqdm
    """

    class TqdmWriteProxy:
        """Proxy that routes writes through tqdm.write()."""

        def __init__(self, original: TextIO, logger: logging.Logger | None = None):
            self.original = original
            self.logger = logger
            self._buffer = io.StringIO()

        def write(self, msg: str) -> int:
            """Write through tqdm.write() to avoid progress bar interference."""
            if not msg or msg.isspace():
                return len(msg) if msg else 0

            # Buffer the message
            self._buffer.write(msg)

            # If we have a complete line, flush it
            if "\n" in msg:
                content = self._buffer.getvalue()
                self._buffer = io.StringIO()

                # Filter and output
                for line in content.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    if FileDescriptorCapture._is_noise(line):
                        continue
                    # Write through tqdm
                    tqdm.write(line, file=sys.stderr)
                    # Also log if logger provided
                    if self.logger:
                        self.logger.debug(f"[captured stdout] {line}")

            return len(msg)

        def flush(self) -> None:
            """Flush any buffered content."""
            content = self._buffer.getvalue()
            if content:
                self._buffer = io.StringIO()
                for line in content.splitlines():
                    line = line.strip()
                    if line and not FileDescriptorCapture._is_noise(line):
                        tqdm.write(line, file=sys.stderr)

        def __getattr__(self, name):
            """Delegate other attributes to original stream."""
            return getattr(self.original, name)

    original_stdout = sys.stdout
    proxy = TqdmWriteProxy(original_stdout, logger_instance)

    try:
        sys.stdout = proxy
        yield
    finally:
        proxy.flush()
        sys.stdout = original_stdout


def setup_tqdm_logging(
    logger_instance: logging.Logger,
    console_level: int = logging.INFO,
    use_tqdm_handler: bool = True,
) -> logging.Handler:
    """
    Configure a logger to use tqdm-compatible console output.

    This replaces any existing StreamHandler with a TqdmLoggingHandler,
    ensuring clean output alongside progress bars.

    Args:
        logger_instance: Logger to configure
        console_level: Minimum level for console output
        use_tqdm_handler: If True, use TqdmLoggingHandler; if False, use regular StreamHandler

    Returns:
        The added console handler
    """
    # Remove any existing StreamHandlers to avoid duplicates
    for handler in logger_instance.handlers[:]:
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler, logging.FileHandler
        ):
            logger_instance.removeHandler(handler)

    # Add tqdm-compatible handler
    if use_tqdm_handler:
        handler = TqdmLoggingHandler(level=console_level)
    else:
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(console_level)

    # Simple format for console
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)

    logger_instance.addHandler(handler)
    return handler
