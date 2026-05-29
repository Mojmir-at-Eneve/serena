import os
import sys

from serena.agent import log


def is_headless_environment() -> bool:
    """
    Detect if we're running in a headless environment where GUI operations would fail.

    Returns True if:
    - No DISPLAY variable on Linux/Unix
    - Running in SSH session
    - Running in WSL without X server
    - Running in Docker container
    """
    # Check if we're on Windows - GUI usually works there
    if sys.platform == "win32":
        return False

    # Check for DISPLAY variable (required for X11)
    if not os.environ.get("DISPLAY"):  # type: ignore
        return True

    # Check for SSH session
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_CLIENT"):
        return True

    # Check for common CI/container environments
    if os.environ.get("CI") or os.environ.get("CONTAINER") or os.path.exists("/.dockerenv"):
        return True

    # Check for WSL (only on Unix-like systems where os.uname exists)
    if hasattr(os, "uname"):
        if "microsoft" in os.uname().release.lower():
            # In WSL, even with DISPLAY set, X server might not be running
            # This is a simplified check - could be improved
            return True

    return False


def show_fatal_exception_safe(e: Exception) -> None:
    """
    Log a fatal exception and print it to stderr.

    GUI log viewer was removed for internal deployment (no Tk dependency in headless MCP use).
    """
    # Log the error and print it to stderr
    log.error(f"Fatal exception: {e}", exc_info=e)
    print(f"Fatal exception: {e}", file=sys.stderr)
