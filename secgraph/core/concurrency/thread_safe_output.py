"""
Thread-safe output suppression for external libraries.

This module provides utilities to suppress stdout/stderr from external libraries
that print from worker threads, ensuring clean progress bar output.
"""

import io
import sys
import threading

# Thread-local storage for stdout/stderr redirection
_thread_local = threading.local()


class ThreadSafeOutputCapture:
    """
    Thread-safe output capture that redirects stdout/stderr per thread.

    This is more reliable than contextlib.redirect_stdout() because:
    1. It works across thread boundaries
    2. It captures output immediately (no buffering delays)
    3. It can be installed in worker threads
    """

    def __init__(self, capture_stdout: bool = True, capture_stderr: bool = True):
        self.capture_stdout = capture_stdout
        self.capture_stderr = capture_stderr
        self.stdout_buffer = io.StringIO()
        self.stderr_buffer = io.StringIO()
        self.original_stdout = None
        self.original_stderr = None
        self._installed = False

    def install(self):
        """Install output redirection for current thread."""
        if self._installed:
            return

        if self.capture_stdout:
            self.original_stdout = sys.stdout
            sys.stdout = self.stdout_buffer

        if self.capture_stderr:
            self.original_stderr = sys.stderr
            sys.stderr = self.stderr_buffer

        self._installed = True

    def uninstall(self):
        """Restore original stdout/stderr for current thread."""
        if not self._installed:
            return

        # Flush buffers before restoring
        if self.capture_stdout:
            self.stdout_buffer.flush()
            sys.stdout = self.original_stdout
            if self.original_stdout:
                self.original_stdout.flush()

        if self.capture_stderr:
            self.stderr_buffer.flush()
            sys.stderr = self.original_stderr
            if self.original_stderr:
                self.original_stderr.flush()

        self._installed = False

    def get_captured_output(self) -> tuple[str, str]:
        """Get captured stdout and stderr content."""
        stdout_content = self.stdout_buffer.getvalue()
        stderr_content = self.stderr_buffer.getvalue()
        return stdout_content, stderr_content

    def clear(self):
        """Clear captured output."""
        self.stdout_buffer.seek(0)
        self.stdout_buffer.truncate(0)
        self.stderr_buffer.seek(0)
        self.stderr_buffer.truncate(0)

    def __enter__(self):
        self.install()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.uninstall()
        return False


def install_thread_output_capture(capture_stdout: bool = True, capture_stderr: bool = True):
    """
    Install thread-local output capture for current thread.

    This should be called at the start of worker threads to capture
    output from external libraries (like datamule).

    Returns:
        ThreadSafeOutputCapture instance (call uninstall() when done)
    """
    if not hasattr(_thread_local, "output_capture"):
        _thread_local.output_capture = ThreadSafeOutputCapture(
            capture_stdout=capture_stdout, capture_stderr=capture_stderr
        )
        _thread_local.output_capture.install()

    return _thread_local.output_capture


def uninstall_thread_output_capture() -> tuple[str, str]:
    """
    Uninstall thread-local output capture for current thread.

    Returns:
        Tuple of (stdout_content, stderr_content) that was captured
    """
    if hasattr(_thread_local, "output_capture"):
        capture = _thread_local.output_capture
        # Get captured output before uninstalling
        stdout, stderr = capture.get_captured_output()
        capture.uninstall()
        # Store captured output in thread-local before deleting capture
        _thread_local.captured_stdout = stdout
        _thread_local.captured_stderr = stderr
        delattr(_thread_local, "output_capture")
        return stdout, stderr
    return "", ""


def get_thread_captured_output() -> tuple[str, str]:
    """
    Get captured output for current thread.

    Works even after uninstall_thread_output_capture() is called.
    """
    if hasattr(_thread_local, "output_capture"):
        # Still installed, get from capture
        result = _thread_local.output_capture.get_captured_output()
        return (str(result[0]), str(result[1]))
    elif hasattr(_thread_local, "captured_stdout"):
        # Uninstalled, get from stored values
        stdout = str(getattr(_thread_local, "captured_stdout", ""))
        stderr = str(getattr(_thread_local, "captured_stderr", ""))
        return (stdout, stderr)
    return "", ""
