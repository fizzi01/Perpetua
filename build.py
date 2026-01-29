
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

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent / "src"))
from utils.logging import get_logger
from config import ApplicationConfig

GUI_EXECUTABLE = ApplicationConfig.app_name.lower()
APP_NAME = ApplicationConfig.app_name


class Builder:

    def __init__(self, project_root: Path, skip_gui: bool = False,
                 skip_daemon: bool = False, clean: bool = False,
                 release: bool = True, nuitka_args: Optional[list[str]] = None):
        self.project_root = project_root
        self.skip_gui = skip_gui
        self.skip_daemon = skip_daemon
        self.clean = clean
        self.release = release
        self.nuitka_args = nuitka_args or []
        self.log = get_logger("build", verbose=True)

        self.gui_dir = project_root / "src-gui"
        build_type = "release" if release else "debug"
        self.icons_dir = self.gui_dir / "src-tauri" / "icons"
        self.gui_exe = self.gui_dir / "src-tauri" / "target" / build_type / GUI_EXECUTABLE
        self.src_dir = project_root / "src"
        self.build_dir = project_root / ".build"

        self.system = sys.platform
        self.is_macos = self.system == "darwin"
        self.is_windows = self.system == "win32"
        self.is_linux = self.system == "linux"

        # Add .exe extension for Windows
        if self.is_windows:
            self.gui_exe = self.gui_exe.with_suffix('.exe')

        if self.is_macos:
            self.icons_dir = self.icons_dir / "macos"

        self._version = self._get_version()

    @property
    def version(self):
        return self._version

    @staticmethod
    def _get_version():
        import tomllib
        pyproject_path = Path(__file__).parent / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            pyproject_data = tomllib.load(f)
        return pyproject_data["project"]["version"]

    def _run(self, cmd: list[str], cwd: Optional[Path] = None, print_cmd: bool = True) -> subprocess.CompletedProcess:
        cwd = cwd or self.project_root
        if print_cmd:
            self.log.debug(f"$ {' '.join(cmd)}")
        return subprocess.run(cmd, cwd=cwd, check=True, capture_output=False, shell=self.is_windows, text=True)

    def _clean(self):
        self.log.info("Cleaning build artifacts")

        if self.build_dir.exists():
            self.log.debug(f"Removing {self.build_dir}")
            shutil.rmtree(self.build_dir)

        gui_dist = self.gui_dir / "dist"
        if gui_dist.exists():
            self.log.debug(f"Removing {gui_dist}")
            shutil.rmtree(gui_dist)

        tauri_target = self.gui_dir / "src-tauri" / "target"
        if tauri_target.exists():
            response = input("Remove Tauri target? [y/N]: ")
            if response.lower() == 'y':
                shutil.rmtree(tauri_target)

    def _build_gui(self) -> int:
        if self.skip_gui:
            return 0

        self.log.info("Building GUI")

        try:
            subprocess.run(["npm", "--version"], shell=self.is_windows, check=True, capture_output=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            self.log.error("npm not found")
            return 1

        try:
            subprocess.run(["cargo", "--version"], shell=self.is_windows, check=True, capture_output=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            self.log.error("cargo not found")
            return 1

        self._run(["npm", "install"], cwd=self.gui_dir)

        # Check if tauri is installed
        try:
            subprocess.run(["cargo", "tauri", "--version"], shell=self.is_windows, check=True, capture_output=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            self.log.info("Installing Tauri CLI")
            res = self._run(["cargo", "install", "tauri-cli", "--version", "^2.0.0", "--locked"], cwd=self.gui_dir)
            if res.returncode != 0:
                self.log.error("Failed to install Tauri CLI")
                return res.returncode

        build_cmd = ["cargo", "tauri", "build"]
        if not self.release:
            build_cmd.append("--debug")

        ret = self._run(build_cmd, cwd=self.gui_dir).returncode
        if ret == 0:
            self.log.info("Copying data files")
            res = self._clean_data_files()
            if res != 0:
                return res
            return self._copy_data_files()
        else:
            return ret

    def _clean_data_files(self):
        try:
            output_exe = self.build_dir / self.gui_exe.name
            if output_exe.exists():
                output_exe.unlink()
        except Exception as e:
            self.log.error(f"Failed to clean data files: {e}")
            return 1
        return 0

    def _copy_data_files(self):
        try:
            output_exe = self.build_dir / self.gui_exe.name
            output_exe.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.gui_exe, output_exe)
            shutil.copystat(self.gui_exe, output_exe)
        except Exception as e:
            self.log.error(f"Failed to copy data files: {e}")
            return 1
        return 0

    def _build_daemon(self) -> int:
        if self.skip_daemon:
            return 0

        self.log.info("Building daemon")

        try:
            subprocess.run([sys.executable, "-m", "nuitka", "--version"],
                         check=True, capture_output=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            self.log.info("Installing Nuitka")
            res = subprocess.run([sys.executable, "-m", "pip", "install", "nuitka"], check=True)
            if res.returncode != 0:
                self.log.error("Failed to install Nuitka")
                return res.returncode

        launcher_py = self.project_root / "launcher.py"
        output_exe = self.build_dir / self.gui_exe.name

        # Check that the GUI executable exists
        if not output_exe.exists():
            # Try to copy data files again
            self.log.warning("GUI executable not found, attempting to copy data files again")
            if self._copy_data_files() != 0:
                self.log.error("GUI executable not found and failed to copy data files")
                return 1

        nuitka_cmd = [
            sys.executable, "-m", "nuitka",
            f"--product-name={APP_NAME}",
            f"--file-version={self.version}",
            f"--product-version={self.version}",
            f"--output-dir={self.build_dir}",
            f"--output-filename={APP_NAME}",
            f"--output-folder-name={APP_NAME}",
            "--include-package=utils",
            "--include-package=input",
            "--python-flag=no_docstrings",
            f"--include-data-files={output_exe}=_{self.gui_exe.name}"
        ]

        if self.is_macos:
            nuitka_cmd.extend([
                "--macos-create-app-bundle",
                f"--macos-app-version={self.version}",
                "--macos-prohibit-multiple-instances",
                f"--macos-sign-identity={APP_NAME}",
                "--macos-app-protected-resource=NSAppleEventsUsageDescription:Automation Control",
                f"--macos-app-name={APP_NAME}",
                f"--macos-app-icon={self.icons_dir / 'icon.icns'}",
            ])

        if self.is_windows:
            nuitka_cmd.extend([
                "--standalone",
                "--windows-console-mode=attach",
                f"--windows-icon-from-ico={self.icons_dir / 'icon.ico'}",
            ])

        nuitka_cmd.extend(self.nuitka_args)
        nuitka_cmd.append(str(launcher_py))
        return self._run(nuitka_cmd, cwd=self.src_dir, print_cmd=False).returncode

    def _sign_bundle(self):
        if self.is_macos:
            self.log.info("Signing MacOS app bundle")
            app_bundle = self.build_dir / f"{APP_NAME}.app"
            sign_cmd = [
                "codesign",
                "--deep",
                "--force",
                "--verify",
                "--verbose",
                "--sign", APP_NAME,
                str(app_bundle)
            ]
            self._run(sign_cmd)

    def _summary(self):
        build_type = 'Release' if self.release else 'Debug'
        self.log.info(f"Platform: {self.system}, Build: {build_type}, Output: {self.build_dir}")

        if self.build_dir.exists():
            for item in sorted(self.build_dir.iterdir()):
                if item.is_file():
                    size_mb = item.stat().st_size / (1024 * 1024)
                    self.log.info(f"  {item.name} ({size_mb:.2f} MB)")
                else:
                    # Directory size
                    total_size = sum(f.stat().st_size for f in item.rglob('*') if f.is_file())
                    size_mb = total_size / (1024 * 1024)
                    self.log.info(f"  {item.name}/ ({size_mb:.2f} MB)")

    def build(self):
        try:
            self.log.info(f"Build started ({self.system})")

            if self.clean:
                self._clean()

            if self._build_gui() != 0:
                raise RuntimeError("GUI build failed")
            if self._build_daemon() != 0:
                raise RuntimeError("Daemon build failed")
            elif not self.skip_daemon:
                self._sign_bundle()
            self._summary()

            self.log.info("Build completed")

        except Exception as e:
            self.log.exception(f"Build failed: {e}")
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Build script")
    parser.add_argument("--skip-gui", action="store_true", help="Skip GUI build")
    parser.add_argument("--skip-daemon", action="store_true", help="Skip daemon build")
    parser.add_argument("--clean", action="store_true", help="Clean before build")
    parser.add_argument("--debug", action="store_true", help="Debug build")
    parser.add_argument("--nuitka-args", nargs=argparse.REMAINDER, default=[], help="Extra arguments for Nuitka")
    args = parser.parse_args()

    builder = Builder(
        project_root=Path(__file__).parent.resolve(),
        skip_gui=args.skip_gui,
        skip_daemon=args.skip_daemon,
        clean=args.clean,
        release=not args.debug,
        nuitka_args=args.nuitka_args
    )
    builder.build()


if __name__ == "__main__":
    main()
