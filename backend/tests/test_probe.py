import hashlib
import hmac
import socket
import struct
import threading

from app.radius import probe

SECRET = b"xyzzy5461"


def test_pap_hide_rfc2865_vector():
    auth = bytes.fromhex("0f403f9473978057bd83d5cb98f4227a")
    assert probe.pap_hide(b"arctangent", SECRET, auth) == bytes.fromhex("0dbe708d93d413ce3196e43f782a0aee")


def test_message_authenticator_first_and_correct():
    auth = bytes(16)
    pkt = probe.build(probe.STATUS_SERVER, 7, SECRET, [(probe.NAS_IDENTIFIER, b"t1")], auth)
    assert pkt[20] == probe.MESSAGE_AUTHENTICATOR and pkt[21] == 18
    zeroed = pkt[:22] + bytes(16) + pkt[38:]
    assert hmac.new(SECRET, zeroed, hashlib.md5).digest() == pkt[22:38]
    assert struct.unpack("!H", pkt[2:4])[0] == len(pkt)


def _responder(secret: bytes, code: int):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))

    def serve():
        req, addr = s.recvfrom(4096)
        attrs = struct.pack("!BB", 18, 4) + b"ok"          # Reply-Message
        head = struct.pack("!BBH", code, req[1], 20 + len(attrs))
        resp_auth = hashlib.md5(head + req[4:20] + attrs + secret).digest()
        s.sendto(head + resp_auth + attrs, addr)
        s.close()

    threading.Thread(target=serve, daemon=True).start()
    return s.getsockname()[1]


def test_access_request_roundtrip_accept():
    port = _responder(SECRET, probe.ACCESS_ACCEPT)
    r = probe.access_request("127.0.0.1", port, SECRET, "u", "p", "192.0.2.1", "t1", timeout=1, tries=1)
    assert r is not None and r.name == "Access-Accept" and (18, b"ok") in r.attrs


def test_wrong_secret_reply_ignored():
    port = _responder(b"other-secret", probe.ACCESS_ACCEPT)
    assert probe.status_server("127.0.0.1", port, SECRET, "t1", timeout=0.5, tries=1) is None
