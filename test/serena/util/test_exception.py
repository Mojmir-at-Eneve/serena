import os
from unittest.mock import MagicMock, Mock, patch

import pytest

from serena.util.exception import is_headless_environment, show_fatal_exception_safe


class TestHeadlessEnvironmentDetection:
    """Test class for headless environment detection functionality."""

    def test_is_headless_no_display(self):
        """Test that environment without DISPLAY is detected as headless on Linux."""
        with patch("sys.platform", "linux"):
            with patch.dict(os.environ, {}, clear=True):
                assert is_headless_environment() is True

    def test_is_headless_ssh_connection(self):
        """Test that SSH sessions are detected as headless."""
        with patch("sys.platform", "linux"):
            with patch.dict(os.environ, {"SSH_CONNECTION": "192.168.1.1 22 192.168.1.2 22", "DISPLAY": ":0"}):
                assert is_headless_environment() is True

            with patch.dict(os.environ, {"SSH_CLIENT": "192.168.1.1 22 22", "DISPLAY": ":0"}):
                assert is_headless_environment() is True

    def test_is_headless_wsl(self):
        """Test that WSL environment is detected as headless."""
        # Skip this test on Windows since os.uname doesn't exist
        if not hasattr(os, "uname"):
            pytest.skip("os.uname not available on this platform")

        with patch("sys.platform", "linux"):
            with patch("os.uname") as mock_uname:
                mock_uname.return_value = Mock(release="5.15.153.1-microsoft-standard-WSL2")
                with patch.dict(os.environ, {"DISPLAY": ":0"}):
                    assert is_headless_environment() is True

    def test_is_headless_docker(self):
        """Test that Docker containers are detected as headless."""
        with patch("sys.platform", "linux"):
            # Test with CI environment variable
            with patch.dict(os.environ, {"CI": "true", "DISPLAY": ":0"}):
                assert is_headless_environment() is True

            # Test with CONTAINER environment variable
            with patch.dict(os.environ, {"CONTAINER": "docker", "DISPLAY": ":0"}):
                assert is_headless_environment() is True

            # Test with .dockerenv file
            with patch("os.path.exists") as mock_exists:
                mock_exists.return_value = True
                with patch.dict(os.environ, {"DISPLAY": ":0"}):
                    assert is_headless_environment() is True

    def test_is_not_headless_windows(self):
        """Test that Windows is never detected as headless."""
        with patch("sys.platform", "win32"):
            # Even without DISPLAY, Windows should not be headless
            with patch.dict(os.environ, {}, clear=True):
                assert is_headless_environment() is False


class TestShowFatalExceptionSafe:
    """Test class for safe fatal exception display functionality."""

    @patch("serena.util.exception.log")
    def test_show_fatal_exception_safe_logs_error(self, mock_log):
        """Test that exceptions are logged."""
        test_exception = ValueError("Test error")
        show_fatal_exception_safe(test_exception)
        mock_log.error.assert_called_once()

    def test_show_fatal_exception_safe_prints_to_stderr(self):
        """Test that exceptions are always printed to stderr."""
        test_exception = ValueError("Test error message")

        with patch("sys.stderr", new_callable=MagicMock) as mock_stderr:
            with patch("serena.util.exception.log"):
                show_fatal_exception_safe(test_exception)

        mock_stderr.write.assert_any_call("Fatal exception: Test error message")
