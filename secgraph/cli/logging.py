"""
Logging utilities for secgraph CLI.

Provides logging setup and header printing functions with tqdm compatibility.
"""

import logging
import sys
import time
from pathlib import Path

from secgraph.utils.tqdm_logging import TqdmLoggingHandler


def setup_logging(
    script_name: str,
    execute: bool = False,
    log_dir: Path = Path("logs"),
    tqdm_compatible: bool = True,
) -> logging.Logger:
    """
    Set up logging for a script.

    Args:
        script_name: Name of the script (for log file naming)
        execute: If True, log to file + console. If False, only console.
        log_dir: Directory for log files
        tqdm_compatible: If True, use TqdmLoggingHandler for clean progress bar output

    Returns:
        Configured logger instance
    """
    # Console formatter: no timestamps (clean output)
    console_formatter = logging.Formatter("%(message)s")

    # CRITICAL: Clear any existing root logger handlers
    # Third-party libraries (e.g., datamule) may call logging.basicConfig() during import
    # which adds a handler with timestamps to the root logger. We need to remove these
    # so that console output is clean (timestamps only in log files).
    root_logger = logging.getLogger()
    root_logger.handlers = []  # Clear root handlers set by third-party libs
    root_logger.setLevel(logging.WARNING)  # Root only logs warnings+

    if execute:
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        log_file = log_dir / f"{script_name}_{timestamp}.log"

        # Create logger with script name (not __name__) for better identification
        logger = logging.getLogger(script_name)
        logger.setLevel(logging.DEBUG)  # Capture all levels
        logger.handlers = []  # Clear any existing handlers

        # Create a custom handler class that flushes after each emit
        # This ensures log messages are written to disk immediately
        class FlushingFileHandler(logging.FileHandler):
            def emit(self, record):
                super().emit(record)
                self.flush()

        # File handler: DEBUG and above (detailed logs with timestamps)
        # Use flushing handler to ensure logs are written immediately
        file_handler = FlushingFileHandler(log_file, mode="a", encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        file_handler.setFormatter(file_formatter)

        # Console handler: INFO and above (clean output, no timestamps)
        # Use TqdmLoggingHandler to avoid interference with tqdm progress bars
        console_handler: logging.Handler
        if tqdm_compatible:
            console_handler = TqdmLoggingHandler(level=logging.INFO)
        else:
            stream_handler = logging.StreamHandler(sys.stderr)
            stream_handler.setLevel(logging.INFO)
            console_handler = stream_handler
        console_handler.setFormatter(console_formatter)

        # Add handlers
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

        # Prevent propagation to root logger (avoid duplicate messages)
        logger.propagate = False

        # Suppress noisy external library loggers
        # These print warnings/errors to console that clutter output
        for noisy_logger in [
            "datamule",  # 10-K downloader library
            "httpx",  # HTTP client
            "openai",  # OpenAI API
            "urllib3",  # HTTP library
            "httpcore",  # HTTP core
            "yfinance",  # Yahoo Finance API
        ]:
            logging.getLogger(noisy_logger).setLevel(logging.ERROR)

        # Configure the secgraph parent logger
        # This ensures child loggers (e.g., secgraph.parsing.*)
        # route through our tqdm-compatible handler instead of the root logger
        pkg_logger = logging.getLogger("secgraph")
        pkg_logger.setLevel(logging.DEBUG)
        pkg_logger.handlers = []  # Clear existing handlers
        pkg_logger.addHandler(file_handler)  # All levels to file
        # Console: Only show INFO, not WARNING (warnings go to file only)
        # This prevents warnings from interrupting tqdm progress bars
        pkg_console_handler = (
            TqdmLoggingHandler(level=logging.INFO)
            if tqdm_compatible
            else logging.StreamHandler(sys.stderr)
        )
        pkg_console_handler.setLevel(logging.ERROR)  # Console shows only ERROR+
        pkg_console_handler.setFormatter(console_formatter)
        pkg_logger.addHandler(pkg_console_handler)
        pkg_logger.propagate = False  # Don't propagate to root

        logger.info(f"Log file: {log_file}")
        return logger
    else:
        # Dry-run mode: simple console logging
        logging.basicConfig(
            level=logging.INFO,
            format="%(message)s",
            stream=sys.stdout,
            force=True,  # Override any existing basicConfig
        )
        return logging.getLogger(__name__)


def print_dry_run_header(title: str, logger: logging.Logger | None = None):
    """
    Print a standard dry-run header.

    Args:
        title: Title for the dry-run section
        logger: Optional logger instance (if None, uses print)
    """
    if logger is None:
        logger = logging.getLogger(__name__)

    logger.info("=" * 70)
    logger.info(f"{title} (Dry Run)")
    logger.info("=" * 70)


def print_execute_header(title: str, logger: logging.Logger | None = None):
    """
    Print a standard execute mode header.

    Args:
        title: Title for the execute section
        logger: Optional logger instance (if None, uses print)
    """
    if logger is None:
        logger = logging.getLogger(__name__)

    logger.info("=" * 70)
    logger.info(title)
    logger.info("=" * 70)
