"""Build scripts wrapper for Poetry."""

#  Perpatua - open-source and cross-platform KVM software.
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

import sys
from build import main as build_main


def build():
    """Run full build."""
    sys.exit(build_main())


def build_gui():
    """Build GUI only."""
    sys.argv.extend(["--skip-daemon"])
    sys.exit(build_main())


def build_daemon():
    """Build daemon only."""
    sys.argv.extend(["--skip-gui"])
    sys.exit(build_main())


def clean_build():
    """Clean and build."""
    sys.argv.extend(["--clean"])
    sys.exit(build_main())
