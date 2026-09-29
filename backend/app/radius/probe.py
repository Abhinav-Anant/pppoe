"""Minimal RADIUS *test client* (RFC 2865, RFC 5997 Status-Server). Stdlib only.

Used by `bngctl radius test` and the RADIUS health check. It is not a RADIUS
server and never logs the secret or the password.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import socket
import struct
import time
from dataclasses import dataclass

ACCESS_REQUEST, ACCESS_ACCEPT, ACCESS_REJECT, ACCESS_CHALLENGE, STATUS_SERVER = 1, 2, 3, 11, 12
USER_NAME, USER_PASSWORD, NAS_IP_ADDRESS, NAS_IDENTIFIER, MESSAGE_AUTHENTICATOR = 1, 2, 4, 32, 80
_NAMES = {ACCESS_ACCEPT: "Access-Accept", ACCESS_REJECT: "Access-Reject", ACCESS_CHALLENGE: "Access-Challenge"}


@dataclass
class Reply:
    code: int
    rtt_ms: float
    attrs: list[tuple[int, bytes]]

    @property
    def name(self) -> str:
        return _NAMES.get(self.code, f"code {self.code}")


def _attr(t: int, value: bytes) -> bytes:
    if len(value) > 253:
        raise ValueError("RADIUS attribute too long")
    return struct.pack("!BB", t, len(value) + 2) + value


def pap_hide(password: bytes, secret: bytes, authenticator: bytes) -> bytes:
    if len(password) > 128:
        raise ValueError("password too long")
    padded = password.ljust(max(16, -(-len(password) // 16) * 16), b"\0")
    out, prev = b"", authenticator
    for i in range(0, len(padded), 16):
        key = hashlib.md5(secret + prev).digest()
        prev = bytes(a ^ b for a, b in zip(padded[i:i + 16], key))
        out += prev
    return out


def build(code: int, ident: int, secret: bytes, attrs: list[tuple[int, bytes]], authenticator: bytes) -> bytes:
    body = _attr(MESSAGE_AUTHENTICATOR, bytes(16)) + b"".join(_attr(t, v) for t, v in attrs)
    header = struct.pack("!BBH", code, ident, 20 + len(body)) + authenticator
    mac = hmac.new(secret, header + body, hashlib.md5).digest()
    return header + body[:2] + mac + body[18:]


def valid_response(resp: bytes, request: bytes, secret: bytes) -> bool:
    if len(resp) < 20 or resp[1] != request[1]:
        return False
    length = struct.unpack("!H", resp[2:4])[0]
    if not 20 <= length <= len(resp):
        return False
    expected = hashlib.md5(resp[:4] + request[4:20] + resp[20:length] + secret).digest()
    return hmac.compare_digest(expected, resp[4:20])


def parse_attrs(data: bytes) -> list[tuple[int, bytes]]:
    attrs, i = [], 0
    while i + 2 <= len(data):
        t, ln = data[i], data[i + 1]
        if ln < 2 or i + ln > len(data):
            break
        attrs.append((t, data[i + 2:i + ln]))
        i += ln
    return attrs


def _send(host: str, port: int, secret: bytes, packet: bytes, timeout: float, tries: int) -> Reply | None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        for _ in range(tries):
            t0 = time.monotonic()
            s.sendto(packet, (host, port))
            try:
                while True:
                    data, addr = s.recvfrom(4096)
                    if addr[0] == host and valid_response(data, packet, secret):
                        length = struct.unpack("!H", data[2:4])[0]
                        return Reply(data[0], (time.monotonic() - t0) * 1000, parse_attrs(data[20:length]))
            except socket.timeout:
                continue
    return None


def status_server(host: str, port: int, secret: bytes, nas_identifier: str,
                  timeout: float = 3.0, tries: int = 2) -> Reply | None:
    auth = os.urandom(16)
    pkt = build(STATUS_SERVER, auth[0], secret, [(NAS_IDENTIFIER, nas_identifier.encode())], auth)
    return _send(host, port, secret, pkt, timeout, tries)


def access_request(host: str, port: int, secret: bytes, user: str, password: str, nas_ip: str,
                   nas_identifier: str, timeout: float = 3.0, tries: int = 2) -> Reply | None:
    auth = os.urandom(16)
    attrs = [(USER_NAME, user.encode()), (USER_PASSWORD, pap_hide(password.encode(), secret, auth)),
             (NAS_IP_ADDRESS, socket.inet_aton(nas_ip)), (NAS_IDENTIFIER, nas_identifier.encode())]
    return _send(host, port, secret, build(ACCESS_REQUEST, auth[0], secret, attrs, auth), timeout, tries)
