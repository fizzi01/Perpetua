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

"""Unit tests for the Linux permission checker and its udev-rule installer.

The module is importable on every platform (``grp`` exists on macOS too), so
these run in the normal matrix rather than only on Linux. The privileged half
lives in ``scripts/enable_uinput.sh`` and is covered by
``scripts/test_udev_setup.sh``, which needs a container.
"""

import subprocess
from argparse import ArgumentParser

import pytest

from utils.cli import DaemonArguments
from utils.permissions import _linux
from utils.permissions._base import PermissionStatus, PermissionType


@pytest.fixture
def fake_script(tmp_path, monkeypatch):
    """A resolvable setup script, so tests exercise the run and not the lookup."""
    script = tmp_path / "enable_uinput.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    monkeypatch.setattr(_linux, "_resolve_setup_script", lambda: script)
    return script


@pytest.fixture
def completed(monkeypatch):
    """Capture the subprocess call and return a canned result."""
    seen = {}

    def _install(returncode=0, stdout="", stderr=""):
        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            seen.update(kwargs)
            return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

        monkeypatch.setattr(_linux.subprocess, "run", fake_run)
        return seen

    return _install


class TestResolveScript:
    def test_found_next_to_the_executable(self, tmp_path, monkeypatch):
        script = tmp_path / "enable_uinput.sh"
        script.write_text("")
        monkeypatch.setattr(_linux.sys, "executable", str(tmp_path / "_perpetua"))

        assert _linux._resolve_setup_script() == script

    def test_appdir_fallback(self, tmp_path, monkeypatch):
        bundle = tmp_path / "usr" / "lib" / "Perpetua"
        bundle.mkdir(parents=True)
        script = bundle / "enable_uinput.sh"
        script.write_text("")
        monkeypatch.setattr(_linux.sys, "executable", str(tmp_path / "nowhere" / "x"))
        monkeypatch.setenv("APPDIR", str(tmp_path))

        assert _linux._resolve_setup_script() == script

    def test_source_checkout_fallback(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_linux.sys, "executable", str(tmp_path / "nowhere" / "x"))
        monkeypatch.delenv("APPDIR", raising=False)

        resolved = _linux._resolve_setup_script()

        assert resolved is not None and resolved.name == "enable_uinput.sh"


class TestEscalationAvailable:
    def test_root_needs_no_prompt(self, monkeypatch):
        monkeypatch.setattr(_linux, "_is_root", lambda: True)
        monkeypatch.setattr(_linux.shutil, "which", lambda _: None)

        assert _linux.escalation_available() is True

    def test_pkexec_in_a_graphical_session(self, monkeypatch):
        monkeypatch.setattr(_linux, "_is_root", lambda: False)
        monkeypatch.setattr(_linux.shutil, "which", lambda n: "/usr/bin/pkexec")
        monkeypatch.setenv("DISPLAY", ":0")

        assert _linux.escalation_available() is True

    def test_sudo_alone_does_not_count(self, monkeypatch):
        """A terminal-only fallback is useless to a GUI with no terminal."""
        monkeypatch.setattr(_linux, "_is_root", lambda: False)
        monkeypatch.setattr(
            _linux.shutil, "which", lambda n: "/usr/bin/sudo" if n == "sudo" else None
        )
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

        assert _linux.escalation_available() is False


class TestInstall:
    def test_runs_the_script(self, fake_script, completed, monkeypatch):
        monkeypatch.setattr(_linux, "escalation_available", lambda: True)
        seen = completed(stdout="done")

        ok, detail = _linux.install_udev_rules()

        assert ok and detail == "done"
        assert seen["argv"] == ["/bin/sh", str(fake_script), "configure"]

    def test_daemon_path_detaches_stdin(self, fake_script, completed, monkeypatch):
        """Without DEVNULL a daemon started from a terminal would let the
        script fall back to sudo, prompting on a tty the GUI user cannot see.
        """
        monkeypatch.setattr(_linux, "escalation_available", lambda: True)
        seen = completed()

        _linux.install_udev_rules(interactive=False)

        assert seen["stdin"] is subprocess.DEVNULL

    def test_interactive_path_keeps_stdin(self, fake_script, completed, monkeypatch):
        monkeypatch.setattr(_linux, "escalation_available", lambda: True)
        seen = completed()

        _linux.install_udev_rules(interactive=True)

        assert seen["stdin"] is None

    def test_refuses_without_escalation(self, fake_script, completed, monkeypatch):
        """No pkexec and no graphical session: nothing is executed."""
        monkeypatch.setattr(_linux, "escalation_available", lambda: False)
        seen = completed()

        ok, detail = _linux.install_udev_rules()

        assert not ok
        assert "argv" not in seen
        assert _linux.MANUAL_INSTRUCTIONS in detail

    def test_interactive_tries_even_without_pkexec(
        self, fake_script, completed, monkeypatch
    ):
        """From a terminal the script's own sudo branch is still worth a go."""
        monkeypatch.setattr(_linux, "escalation_available", lambda: False)
        seen = completed()

        ok, _ = _linux.install_udev_rules(interactive=True)

        assert ok and seen["argv"][0] == "/bin/sh"

    @pytest.mark.parametrize(
        "code,expected",
        [(126, "dismissed"), (127, "Authentication failed")],
    )
    def test_pkexec_exit_codes_are_translated(
        self, fake_script, completed, monkeypatch, code, expected
    ):
        monkeypatch.setattr(_linux, "escalation_available", lambda: True)
        completed(returncode=code, stderr="pkexec: boom")

        ok, detail = _linux.install_udev_rules()

        assert not ok and expected in detail

    def test_other_failures_report_stderr(self, fake_script, completed, monkeypatch):
        monkeypatch.setattr(_linux, "escalation_available", lambda: True)
        completed(returncode=1, stderr="could not write the rule")

        ok, detail = _linux.install_udev_rules()

        assert not ok and "could not write the rule" in detail

    def test_timeout_is_reported(self, fake_script, monkeypatch):
        monkeypatch.setattr(_linux, "escalation_available", lambda: True)

        def fake_run(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, 1)

        monkeypatch.setattr(_linux.subprocess, "run", fake_run)

        ok, detail = _linux.install_udev_rules()

        assert not ok and "Timed out" in detail

    def test_missing_script_reports_manual_instructions(self, monkeypatch):
        monkeypatch.setattr(_linux, "_resolve_setup_script", lambda: None)

        ok, detail = _linux.install_udev_rules()

        assert not ok and _linux.MANUAL_INSTRUCTIONS in detail

    def test_second_concurrent_call_is_refused(self, fake_script, monkeypatch):
        """Two clicks on the gate button must not stack two polkit dialogs."""
        assert _linux._install_lock.acquire(blocking=False)
        try:
            ok, detail = _linux.install_udev_rules()
        finally:
            _linux._install_lock.release()

        assert not ok and "already in progress" in detail

    def test_the_lock_is_released_on_failure(self, monkeypatch):
        monkeypatch.setattr(_linux, "_resolve_setup_script", lambda: None)

        _linux.install_udev_rules()

        assert _linux._install_lock.acquire(blocking=False)
        _linux._install_lock.release()


class TestHasInputAccess:
    @staticmethod
    def _state(monkeypatch, *, uinput, dumpkeys=True, console=True, tty_rule=True):
        monkeypatch.setattr(_linux, "_is_root", lambda: False)
        monkeypatch.setattr(_linux, "_can_write_uinput", lambda: uinput)
        monkeypatch.setattr(_linux, "_has_dumpkeys", lambda: dumpkeys)
        monkeypatch.setattr(_linux, "_can_read_console", lambda: console)
        monkeypatch.setattr(_linux, "_tty_rule_installed", lambda: tty_rule)

    def test_writable_uinput_with_console_is_enough(self, monkeypatch):
        self._state(monkeypatch, uinput=True, tty_rule=False)

        assert _linux._has_input_access() is True

    def test_tty_rule_substitutes_for_console(self, monkeypatch):
        """The uaccess ACL only reaches the active session."""
        self._state(monkeypatch, uinput=True, console=False, tty_rule=True)

        assert _linux._has_input_access() is True

    def test_unwritable_uinput_denies(self, monkeypatch):
        self._state(monkeypatch, uinput=False)

        assert _linux._has_input_access() is False

    def test_missing_dumpkeys_denies(self, monkeypatch):
        self._state(monkeypatch, uinput=True, dumpkeys=False)

        assert _linux._has_input_access() is False

    def test_root_short_circuits(self, monkeypatch):
        self._state(monkeypatch, uinput=False, dumpkeys=False)
        monkeypatch.setattr(_linux, "_is_root", lambda: True)

        assert _linux._has_input_access() is True


class TestCheckPermission:
    def test_denied_is_requestable_when_we_can_elevate(self, monkeypatch):
        monkeypatch.setattr(_linux, "_has_input_access", lambda: False)
        monkeypatch.setattr(_linux, "escalation_available", lambda: True)

        result = _linux.PermissionChecker(log=False).check_permission(
            PermissionType.KEYBOARD_INPUT
        )

        assert result.status is PermissionStatus.DENIED
        assert result.can_request is True

    def test_denied_falls_back_to_manual_instructions(self, monkeypatch):
        monkeypatch.setattr(_linux, "_has_input_access", lambda: False)
        monkeypatch.setattr(_linux, "escalation_available", lambda: False)

        result = _linux.PermissionChecker(log=False).check_permission(
            PermissionType.KEYBOARD_INPUT
        )

        assert result.can_request is False
        assert _linux.MANUAL_INSTRUCTIONS in result.message


class TestRequestPermission:
    def test_granted_is_a_no_op(self, monkeypatch):
        """An already-granted permission must not raise a dialog."""
        called = []
        monkeypatch.setattr(_linux, "_has_input_access", lambda: True)
        monkeypatch.setattr(
            _linux, "install_udev_rules", lambda **kw: called.append(kw) or (True, "")
        )

        result = _linux.PermissionChecker(log=False).request_permission(
            PermissionType.KEYBOARD_INPUT
        )

        assert result.status is PermissionStatus.GRANTED
        assert not called

    def test_installs_then_rechecks(self, monkeypatch):
        access = iter([False, True])
        monkeypatch.setattr(_linux, "_has_input_access", lambda: next(access))
        monkeypatch.setattr(_linux, "install_udev_rules", lambda **kw: (True, "done"))

        result = _linux.PermissionChecker(log=False).request_permission(
            PermissionType.KEYBOARD_INPUT
        )

        assert result.status is PermissionStatus.GRANTED

    def test_rules_written_but_access_pending_mentions_logout(self, monkeypatch):
        monkeypatch.setattr(_linux, "_has_input_access", lambda: False)
        monkeypatch.setattr(_linux, "install_udev_rules", lambda **kw: (True, "done"))
        monkeypatch.setattr(_linux, "escalation_available", lambda: True)

        result = _linux.PermissionChecker(log=False).request_permission(
            PermissionType.KEYBOARD_INPUT
        )

        assert result.status is PermissionStatus.DENIED
        assert "Log out" in result.message

    def test_failure_is_passed_through(self, monkeypatch):
        monkeypatch.setattr(_linux, "_has_input_access", lambda: False)
        monkeypatch.setattr(
            _linux, "install_udev_rules", lambda **kw: (False, "dialog dismissed")
        )
        monkeypatch.setattr(_linux, "escalation_available", lambda: True)

        result = _linux.PermissionChecker(log=False).request_permission(
            PermissionType.KEYBOARD_INPUT
        )

        assert result.message == "dialog dismissed"

    def test_other_permissions_are_only_rechecked(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            _linux, "install_udev_rules", lambda **kw: called.append(kw) or (True, "")
        )

        _linux.PermissionChecker(log=False).request_permission(
            PermissionType.MOUSE_INPUT
        )

        assert not called


class TestCliFlag:
    def test_flag_is_declared_in_daemon_arguments(self):
        """The flag belongs to the daemon binary, not to the launcher parser."""
        parser = ArgumentParser()
        DaemonArguments(parent=parser)

        assert parser.parse_args([]).install_udev_rules is False
        assert parser.parse_args(["--install-udev-rules"]).install_udev_rules is True

    def test_launcher_exits_without_starting_the_daemon(self, monkeypatch):
        import launcher

        started = []
        monkeypatch.setattr(launcher.sys, "argv", ["_perpetua", "--install-udev-rules"])
        monkeypatch.setattr(launcher, "_run_udev_setup", lambda: 0)
        monkeypatch.setattr(launcher.DaemonRunner, "run", lambda self: started.append(1))

        with pytest.raises(SystemExit) as exc:
            launcher.main()

        assert exc.value.code == 0
        assert not started
