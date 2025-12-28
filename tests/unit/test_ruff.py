"""
Runs Ruff linter on project files to ensure code quality and adherence to style guidelines.
"""
import subprocess
import sys

# Directories to check
DIRECTORIES_TO_CHECK = ["command/", "config/", "event/", "input/", "model/", "network/", "service/", "utils/"]


def test_ruff_check():
    """Test that Ruff linter passes on all project files."""
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check"] + DIRECTORIES_TO_CHECK,
        capture_output=True,
        text=True
    )

    assert result.returncode == 0, (
        f"Ruff found issues:\n{result.stdout}\n{result.stderr}"
    )


def test_ruff_format_check():
    """Test that code formatting follows Ruff rules."""
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--check"] + DIRECTORIES_TO_CHECK,
        capture_output=True,
        text=True
    )

    assert result.returncode == 0, (
        f"Ruff format check failed:\n{result.stdout}\n{result.stderr}"
    )