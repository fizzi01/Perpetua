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

import asyncio
import ssl
import time
from typing import Optional, Callable

from model.client import ClientObj

# OpenSSL X509_V_FLAG_NO_CHECK_TIME. Not exposed by Python's ``ssl.VerifyFlags``
# enum, but ``SSLContext.verify_flags`` accepts the raw bit and forwards it to
# ``X509_VERIFY_PARAM_set_flags``. Setting it disables the certificate
# notBefore/notAfter validity-window check during the handshake while leaving
# chain, signature, hostname and CERT_REQUIRED verification fully intact.
#
# We need this because peers on a LAN frequently run without NTP: a client whose
# clock trails the server rejects a perfectly good server certificate with
# "certificate is not yet valid". Trust in this deployment comes from the pinned
# private CA + OTP pairing + private-key possession, not from validity windows.
# Expiry is re-enforced separately (see :func:`peer_cert_is_expired`) so that
# genuinely expired certificates are still rejected.
SSL_NO_CHECK_TIME = 0x200000


def apply_skew_tolerant_time_policy(context: ssl.SSLContext) -> None:
    """Disable OpenSSL's built-in cert validity-window check on ``context``.

    Drops both the not-yet-valid and expired checks at the OpenSSL layer so a
    clock-skewed peer can still complete the handshake. Callers that want to
    keep rejecting expired certificates must re-check expiry after the handshake
    with :func:`peer_cert_is_expired`.
    """
    context.verify_flags |= SSL_NO_CHECK_TIME


def peer_cert_is_expired(ssl_object: Optional[ssl.SSLObject]) -> bool:
    """Return True if the verified peer certificate is past its ``notAfter``.

    Re-enforces expiry only — ``notBefore`` is deliberately ignored, which is
    what lets a behind-the-clock peer connect. The comparison uses the local
    (possibly skewed) clock; that is acceptable, since a behind peer merely
    tolerates the certificate slightly longer while a genuinely expired one is
    still rejected. Returns False when there is no TLS layer or no peer cert
    (nothing to enforce).
    """
    if ssl_object is None:
        return False
    try:
        peer_cert = ssl_object.getpeercert()
    except Exception:
        return False
    if not peer_cert:
        return False
    not_after = peer_cert.get("notAfter")
    if not not_after:
        return False
    try:
        expires_at = ssl.cert_time_to_seconds(not_after)
    except ValueError:
        return False
    return time.time() > expires_at


class CallbackError(Exception):
    """Custom exception for callback invocation errors."""

    pass


class BaseConnectionHandler:
    """
    Base class for connection handlers.
    """

    @staticmethod
    async def _invoke_callback(
        callback: Optional[Callable],
        client: Optional["ClientObj"],
        **kwargs,
    ):
        """
        Invokes a provided callback function with the given client and streams. If the callback
        is a coroutine function, it will be awaited; otherwise, it will be executed
        synchronously.

        Args:
            callback (Optional[Callable]): A function to process the client and streams.
                Can be a coroutine or a standard function.
            client (ClientObj): The client object to pass to the callback.
            streams (list[int]): A list of stream identifiers to pass to the callback.

        Raises:
            CallbackError: If an exception occurs during the execution of the callback.
        """
        if not callback:
            return

        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(client, **kwargs)
            else:
                callback(client, **kwargs)
        except Exception as e:
            raise CallbackError(f"{e}") from e

    async def handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """
        Handle a new connection.

        Args:
            reader (asyncio.StreamReader): The stream reader for the connection.
            writer (asyncio.StreamWriter): The stream writer for the connection.
        """
        raise NotImplementedError("handle_connection must be implemented by subclasses")
