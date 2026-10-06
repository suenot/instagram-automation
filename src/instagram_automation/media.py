"""Snapshot and validate the exact bytes that will reach the upload control."""

import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import socket
import ssl
import stat
import subprocess
import time
from queue import Queue, Empty
from threading import Thread, Timer
from urllib.parse import urljoin, urlsplit

from .errors import PublishError

MAX_BYTES = 1024 ** 3
DOWNLOAD_SECONDS = 120


def source_name(value: str) -> str:
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError as exc:
        raise PublishError("INVALID_MEDIA_URL", "Media URL is malformed.") from exc
    if url.scheme:
        if url.scheme != "https":
            raise PublishError("INVALID_MEDIA_URL", "Remote media must use HTTPS.")
        if not url.hostname or url.username or url.password or port not in {None, 443}:
            raise PublishError("INVALID_MEDIA_URL", "HTTPS media must use a hostname and port 443 without credentials.")
        return value
    return str(Path(value).expanduser().absolute())


def read_caption(caption=None, caption_file=None, metadata_file=None) -> str:
    try:
        if caption is not None:
            return caption
        if caption_file:
            with Path(caption_file).open(encoding="utf-8", newline="") as stream:
                return stream.read()
        from metadata_automation import validate
        envelope = json.loads(Path(metadata_file).read_text(encoding="utf-8"))
        validate(envelope)
        return envelope["platforms"]["instagram"]["caption"]
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        raise PublishError("INVALID_CAPTION", f"Caption or complete metadata envelope is invalid ({type(exc).__name__}).") from exc


def remaining(deadline):
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise PublishError("MEDIA_DOWNLOAD_LIMIT", "HTTPS media exceeded the total 120 second deadline.")
    return seconds


def public_addresses(host: str, deadline=None):
    deadline = deadline if deadline is not None else time.monotonic() + DOWNLOAD_SECONDS
    completed = Queue(maxsize=1)
    def resolve():
        try:
            completed.put((socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM), None))
        except Exception as exc:
            completed.put((None, exc))
    # getaddrinfo has no timeout API. A daemon worker bounds the caller without
    # waiting indefinitely for a stalled OS resolver at process shutdown.
    Thread(target=resolve, daemon=True).start()
    try:
        resolved, error = completed.get(timeout=remaining(deadline))
        remaining(deadline)
        if error:
            raise error
        addresses = list(dict.fromkeys(item[4][0] for item in resolved))
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise PublishError("UNSAFE_MEDIA_URL", "HTTPS media must resolve only to public IP addresses.")
        return addresses
    except Empty as exc:
        raise PublishError("MEDIA_DOWNLOAD_LIMIT", "HTTPS DNS resolution exceeded the total deadline.") from exc
    except OSError as exc:
        raise PublishError("MEDIA_DOWNLOAD_FAILED", "HTTPS media hostname could not be resolved.") from exc


class PublicHTTPSConnection(http.client.HTTPSConnection):
    """Connect to the validated address; TLS still validates the original hostname."""

    def __init__(self, *args, deadline, **kwargs):
        super().__init__(*args, **kwargs)
        self.deadline = deadline
        self.active_socket = None
        self.watchdog = Timer(remaining(deadline), self.expire)
        self.watchdog.daemon = True
        self.watchdog.start()

    def expire(self):
        # Abort even a slow-drip response whose individual reads never time out.
        if self.active_socket is not None:
            try:
                # SSL's shutdown checks its logical closed flag, even while a
                # response makefile retains the FD. Use the base socket method
                # so Connection: close cannot leave a slow read unbounded.
                socket.socket.shutdown(self.active_socket, socket.SHUT_RDWR)
            except OSError:
                pass
        super().close()

    def close(self):
        # http.client calls close when a response declares Connection: close.
        # Its response file can still be reading; retain the deadline watchdog.
        super().close()

    def finish(self):
        self.watchdog.cancel()
        super().close()
        if self.active_socket is not None:
            self.active_socket.close()
        self.active_socket = None

    def connect(self):
        addresses = public_addresses(self.host, self.deadline)
        last_error = None
        for address in addresses:
            raw = None
            try:
                raw = socket.create_connection((address, self.port), timeout=remaining(self.deadline))
                self.active_socket = raw
                raw.settimeout(remaining(self.deadline))
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host, do_handshake_on_connect=False)
                self.active_socket = self.sock
                self.sock.settimeout(remaining(self.deadline))
                self.sock.do_handshake()
                remaining(self.deadline)
                return
            except OSError as exc:
                if self.sock is not None:
                    self.sock.close()
                    self.sock = None
                self.active_socket = None
                if raw is not None:
                    raw.close()
                last_error = exc
        raise last_error


def download(source: str, destination: Path) -> str:
    deadline = time.monotonic() + DOWNLOAD_SECONDS
    url = source
    try:
        for _ in range(6):
            source_name(url)
            parsed = urlsplit(url)
            connection = PublicHTTPSConnection(parsed.hostname, deadline=deadline,
                                               timeout=remaining(deadline), context=ssl.create_default_context())
            response = None
            try:
                target = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
                connection.request("GET", target,
                                   headers={"User-Agent": "igpub/0.1", "Accept-Encoding": "identity"})
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location:
                        raise PublishError("MEDIA_DOWNLOAD_FAILED", "HTTPS media redirect has no location.")
                    url = urljoin(url, location)
                    continue
                if response.status != 200:
                    raise PublishError("MEDIA_DOWNLOAD_FAILED", "HTTPS media server did not return a complete file.")
                length = response.getheader("Content-Length")
                if length is not None and (not length.isdigit() or int(length) > MAX_BYTES):
                    raise PublishError("MEDIA_TOO_LARGE", "Media exceeds the 1 GiB download limit.")
                with destination.open("wb") as outgoing:
                    destination.chmod(0o600)
                    size = 0
                    digest = hashlib.sha256()
                    while True:
                        seconds = remaining(deadline)
                        if connection.active_socket:
                            connection.active_socket.settimeout(seconds)
                        chunk = response.read(64 * 1024)
                        remaining(deadline)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > MAX_BYTES or time.monotonic() > deadline:
                            raise PublishError("MEDIA_DOWNLOAD_LIMIT", "HTTPS media exceeded the byte or time limit.")
                        outgoing.write(chunk)
                        digest.update(chunk)
                if not size or (length is not None and size != int(length)):
                    raise PublishError("INVALID_MEDIA", "HTTPS media file is empty or incomplete.")
                remaining(deadline)
                return digest.hexdigest()
            finally:
                if response is not None:
                    response.close()
                connection.finish()
        raise PublishError("MEDIA_DOWNLOAD_FAILED", "HTTPS media exceeded the redirect limit.")
    except PublishError:
        raise
    except (OSError, http.client.HTTPException) as exc:
        remaining(deadline)
        raise PublishError("MEDIA_DOWNLOAD_FAILED", "HTTPS media could not be downloaded within its limits.") from exc


def snapshot(source: str, destination: Path) -> str:
    if source.startswith("https:"):
        return download(source, destination)
    try:
        with os.fdopen(os.open(source, os.O_RDONLY | os.O_NONBLOCK), "rb") as incoming:
            info = os.fstat(incoming.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
                raise PublishError("INVALID_MEDIA", "Media must be a nonempty regular file of at most 1 GiB.")
            with destination.open("wb") as outgoing:
                destination.chmod(0o600)
                digest, size = hashlib.sha256(), 0
                while chunk := incoming.read(64 * 1024):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise PublishError("MEDIA_TOO_LARGE", "Media exceeds the 1 GiB limit.")
                    outgoing.write(chunk)
                    digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        raise PublishError("INVALID_MEDIA", "Media must be a readable regular local file.") from exc


def validate_media(path: Path, kind: str) -> str:
    if kind == "reel":
        try:
            process = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                                     capture_output=True, timeout=30, check=True)
            data = json.loads(process.stdout)
            video = next(stream for stream in data["streams"] if stream["codec_type"] == "video")
            if (float(data["format"]["duration"]) <= 0 or min(video["width"], video["height"]) <= 0
                    or video.get("codec_name") != "h264" or "mp4" not in data["format"]["format_name"].split(",")):
                raise PublishError("INVALID_MEDIA", "Reels require a valid MP4 video with H.264 video.")
            if video["width"] * 16 != video["height"] * 9:
                raise PublishError("INVALID_REEL_ASPECT", "Reels require full-frame vertical 9:16 source media; prepare it before upload.")
            return ".mp4"
        except FileNotFoundError as exc:
            raise PublishError("FFPROBE_MISSING", "Install ffmpeg (ffprobe) before publishing Reels.", 3) from exc
        except (subprocess.SubprocessError, ValueError, KeyError, StopIteration, TypeError) as exc:
            raise PublishError("INVALID_MEDIA", "ffprobe could not validate this Reel.") from exc
    try:
        from PIL import Image
        with Image.open(path) as image:
            if image.format not in {"JPEG", "PNG"} or min(image.size) <= 0:
                raise PublishError("INVALID_MEDIA", "Photo and carousel media must be JPEG or PNG images.")
            extension = ".jpg" if image.format == "JPEG" else ".png"
            image.verify()
        return extension
    except (OSError, ValueError) as exc:
        raise PublishError("INVALID_MEDIA", "Image file is invalid.") from exc
