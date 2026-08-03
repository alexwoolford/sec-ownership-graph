"""
Security utilities for secgraph.

Provides:
- Path validation (prevent path traversal attacks)
- Text hashing (SHA256 for change detection)
- Tar member validation (prevent Tar Slip attacks)
"""

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# =============================================================================
# Text Hashing
# =============================================================================


def compute_text_hash(text: str) -> str:
    """
    Compute SHA256 hash of text for change detection.

    Args:
        text: Text to hash

    Returns:
        SHA256 hex digest of normalized text, or empty string if text is empty

    Example:
        >>> compute_text_hash("Hello World")
        '2c74fd17edafd80e8447b0d46741ee243b7eb74dd2149a0ab1b9246fb30382f2'
        >>> compute_text_hash("")
        ''
    """
    if not text:
        return ""
    normalized = text.strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# =============================================================================
# Path Validation (Prevent Path Traversal)
# =============================================================================


def validate_path_within_base(
    file_path: Path, base_dir: Path, logger_instance: logging.Logger | None = None
) -> bool:
    """
    Validate that file_path is within base_dir to prevent path traversal attacks.

    This function resolves both paths and ensures the file_path is a subdirectory
    or file within base_dir. This prevents attacks using `../` sequences.

    Args:
        file_path: Path to validate
        base_dir: Base directory that file_path must be within
        logger_instance: Optional logger instance for warnings (defaults to module logger)

    Returns:
        True if path is safe (within base_dir), False otherwise

    Example:
        >>> validate_path_within_base(Path("/data/file.txt"), Path("/data"))
        True
        >>> validate_path_within_base(Path("/etc/passwd"), Path("/data"))
        False
        >>> validate_path_within_base(Path("/data/../etc/passwd"), Path("/data"))
        False
    """
    _log = logger_instance if logger_instance is not None else logger

    try:
        resolved_path = file_path.resolve()
        resolved_base = base_dir.resolve()
        resolved_path.relative_to(resolved_base)
        return True
    except (ValueError, OSError):
        _log.warning(f"Path traversal attempt detected: {file_path} (base: {base_dir})")
        return False


# =============================================================================
# Tar Archive Validation (Prevent Tar Slip Attacks)
# =============================================================================


def validate_tar_member_path(
    member_name: str, extract_dir: Path, logger_instance: logging.Logger | None = None
) -> tuple[bool, str | None]:
    """
    Validate tar member path and return safe filename.

    This function prevents Tar Slip attacks by:
    1. Extracting just the filename (no directory components)
    2. Checking for path traversal attempts (.., absolute paths, separators)
    3. Validating the resolved path is within extract_dir

    Args:
        member_name: Original tar member name
        extract_dir: Directory where files will be extracted
        logger_instance: Optional logger instance (defaults to module logger)

    Returns:
        Tuple of (is_valid, safe_name) where:
        - is_valid: True if path is safe to extract
        - safe_name: Safe filename to use (None if invalid)

    Example:
        >>> validate_tar_member_path("file.txt", Path("/tmp/extract"))
        (True, "file.txt")
        >>> validate_tar_member_path("../../../etc/passwd", Path("/tmp/extract"))
        (False, None)
        >>> validate_tar_member_path("/etc/passwd", Path("/tmp/extract"))
        (False, None)
    """
    _log = logger_instance if logger_instance is not None else logger

    # Check original member_name for path traversal attempts BEFORE extracting filename
    if ".." in member_name:
        _log.warning(f"  ⚠️  Skipping suspicious tar member: {member_name}")
        return (False, None)

    member_path = Path(member_name)
    safe_name = member_path.name  # Get just the filename, no path

    # Enhanced validation: check for absolute paths and leading slashes
    if safe_name.startswith("/") or member_path.is_absolute():
        _log.warning(f"  ⚠️  Skipping suspicious tar member: {member_name}")
        return (False, None)

    # Additional check: ensure safe_name doesn't contain path separators
    if "/" in safe_name or "\\" in safe_name:
        _log.warning(f"  ⚠️  Skipping tar member with path separators: {member_name}")
        return (False, None)

    # Extract to a safe path within extract_dir
    safe_extract_path = extract_dir / safe_name
    # Double-check: ensure resolved path is within extract_dir
    try:
        resolved_path = safe_extract_path.resolve()
        resolved_base = extract_dir.resolve()
        resolved_path.relative_to(resolved_base)
        return (True, safe_name)
    except (ValueError, OSError):
        _log.warning(f"  ⚠️  Path traversal attempt detected: {member_name}")
        return (False, None)
