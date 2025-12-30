"""
Runs Ruff linter on project files to ensure code quality and adherence to style guidelines.
"""

from os import path

import subprocess
import sys

# Directories to check
DIRECTORIES_TO_CHECK = [
    "command/",
    "config/",
    "event/",
    "input/",
    "model/",
    "network/",
    "service/",
    "utils/",
]


def test_ruff_check(project_root_dir):
    """Test that Ruff linter passes on all project files."""
    # remap directories to check with project root
    dirs = [path.join(project_root_dir, s) for s in DIRECTORIES_TO_CHECK]
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check"] + dirs,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"Ruff found issues:\n{result.stdout}\n{result.stderr}"
    )


def test_ruff_format_check(project_root_dir):
    """Test that code formatting follows Ruff rules."""
    dirs = [path.join(project_root_dir, s) for s in DIRECTORIES_TO_CHECK]
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--check"] + dirs,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"Ruff format check failed:\n{result.stdout}\n{result.stderr}"
    )
