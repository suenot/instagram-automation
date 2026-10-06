import socket
import ssl
import time

import pytest

from instagram_automation import media
from instagram_automation.errors import PublishError


def test_dns_obeys_total_deadline(monkeypatch):
    def stalled(*_args, **_kwargs):
        time.sleep(0.2)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
    monkeypatch.setattr(socket, "getaddrinfo", stalled)
    start = time.monotonic()
    with pytest.raises(PublishError) as error:
        media.public_addresses("example.com", start + 0.02)
    assert error.value.code == "MEDIA_DOWNLOAD_LIMIT"
    assert time.monotonic() - start < 0.15


def test_ip_attempts_share_one_deadline(monkeypatch):
    monkeypatch.setattr(media, "public_addresses", lambda *_: ["8.8.8.8", "1.1.1.1"])
    attempts = []
    def connect(address, timeout):
        attempts.append((address, timeout))
        time.sleep(timeout + 0.005)
        raise TimeoutError()
    monkeypatch.setattr(socket, "create_connection", connect)
    start = time.monotonic()
    connection = media.PublicHTTPSConnection("example.com", deadline=start + 0.03,
                                            context=ssl.create_default_context())
    try:
        with pytest.raises(PublishError) as error:
            connection.connect()
        assert error.value.code == "MEDIA_DOWNLOAD_LIMIT"
        assert len(attempts) == 1
        assert time.monotonic() - start < 0.2
    finally:
        connection.finish()


def test_redirect_uses_same_deadline_and_rejects_http(tmp_path, monkeypatch):
    deadlines = []
    class Response:
        status = 302
        def getheader(self, _name):
            return "http://example.com/file"
        def close(self):
            pass
    class Connection:
        def __init__(self, *_args, deadline, **_kwargs):
            deadlines.append(deadline)
        def request(self, *_args, **_kwargs):
            pass
        def getresponse(self):
            return Response()
        def finish(self):
            pass
    monkeypatch.setattr(media, "PublicHTTPSConnection", Connection)
    with pytest.raises(PublishError) as error:
        media.download("https://example.com/video?token=literal", tmp_path / "download")
    assert error.value.code == "INVALID_MEDIA_URL"
    assert len(deadlines) == 1


def test_connection_close_response_does_not_cancel_deadline_watchdog():
    received, sender = socket.socketpair()
    class GuardedSocket(socket.socket):
        def shutdown(self, how):
            if self._closed:
                raise OSError("Logically closed, as in SSLSocket.shutdown")
            super().shutdown(how)
    transport = GuardedSocket(fileno=received.detach())
    response_file = transport.makefile("rb")
    connection = media.PublicHTTPSConnection("example.com", deadline=time.monotonic() + 0.04,
                                            context=ssl.create_default_context())
    connection.active_socket = connection.sock = transport
    try:
        connection.close()  # http.client detaches a Connection: close response.
        started = time.monotonic()
        assert response_file.read(1) == b""
        assert time.monotonic() - started < 0.2
    finally:
        response_file.close()
        sender.close()
        connection.finish()
