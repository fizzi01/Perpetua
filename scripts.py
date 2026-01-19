"""Build scripts wrapper for Poetry."""
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
