"""
Runs Ruff linter on project files to ensure code quality and adherence to style guidelines.
"""


#  Perpetua - open-source and cross-platform KVM software.
#  Copyright (c) 2026 Federico Izzi.
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.
#

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
