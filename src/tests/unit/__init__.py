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

# Mocking pynput to prevent crash on Linux
def _MOCK_PYNPUT():
    import sys
    from unittest.mock import MagicMock

    sys.modules["pynput._util.xorg"] = MagicMock()
    sys.modules["pynput.keyboard._xorg"] = MagicMock()
    sys.modules["pynput.mouse._xorg"] = MagicMock()
    sys.modules["pynput.keyboard._uinput"] = MagicMock()

    # On platforms whose real pynput backend is unavailable (headless Linux),
    # the mocked submodules above make ``pynput.keyboard.Key`` / ``KeyCode``
    # resolve to ``MagicMock`` attributes. Those are not real types, so
    # ``isinstance(key, Key)`` deep in the input layer raises ``TypeError``.
    # Substitute pynput's own import-safe dummy backend (real ``Key``/
    # ``KeyCode`` types) whenever the resolved symbols aren't real types.
    # macOS/Windows keep their real backend untouched.
    from pynput.keyboard._dummy import Key as _DummyKey, KeyCode as _DummyKeyCode
    import pynput.keyboard as _kb

    if not isinstance(getattr(_kb, "Key", None), type):
        _kb.Key = _DummyKey
        _kb.KeyCode = _DummyKeyCode

    # ``input.keyboard.backend._uinput`` imports these names directly.
    _uinput = sys.modules["pynput.keyboard._uinput"]
    _uinput.Key = _DummyKey
    _uinput.KeyCode = _DummyKeyCode
    _uinput.LAYOUT = {}
