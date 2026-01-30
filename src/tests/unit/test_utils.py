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

import utils.net

def test_get_local_ip():
    ip = utils.net.get_local_ip()
    assert ip is not None, "Local IP address should not be None"
    octets = ip.split('.')
    assert len(octets) == 4, "IP address should have 4 octets"
    # It should not be a loopback address
    assert not ip.startswith("127."), "Local IP address should not be a loopback address"
    # It should not be a 0.0.0.0
    assert ip != "0.0.0.0", "Local IP address should not be 0.0.0.0"
    for octet in octets:
        assert 0 <= int(octet) <= 255, f"Each octet should be between 0 and 255, got {octet}"

def test_get_local_ip_exception():
    import socket
    original_socket = socket.socket

    class FailingSocket:
        def __init__(self, *args, **kwargs):
            pass
        def connect(self, address):
            raise Exception("Simulated failure")
        def getsockname(self):
            return ("", 0)
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    socket.socket = FailingSocket
    try:
        try:
            utils.net.get_local_ip()
            assert False, "Expected MissingIpError to be raised"
        except utils.net.MissingIpError as e:
            assert "Could not determine local IP address" in str(e), "Error message should indicate failure to determine IP"
    finally:
        socket.socket = original_socket
