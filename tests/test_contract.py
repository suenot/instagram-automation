from argparse import Namespace
from contextlib import asynccontextmanager
from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from instagram_automation.browser import post_url
from instagram_automation.cli import main
from instagram_automation.errors import PublishError
from instagram_automation.media import public_addresses, read_caption, snapshot, source_name
from instagram_automation.service import normalized, publish
from instagram_automation.state import State


def args(tmp_path, **changes):
    video = tmp_path / "source.mp4"
    if not video.exists():
        video.write_bytes(b"first video")
    values = dict(account="caribbean", request_id="one", kind="reel", video=str(video), media=None,
                  caption="  Exact\nCaption #tag\n", caption_file=None, metadata_file=None,
                  headless=True, timeout_seconds=30)
    values.update(changes)
    return Namespace(**values)


@asynccontextmanager
async def fake_session(*_):
    yield object()


class Adapter:
    clicks = 0
    fail = None
    seen = None
    post_url = None

    def __init__(self, *_):
        pass

    async def open_editor(self, *_):
        pass

    async def existing_urls(self, *_):
        return {"https://www.instagram.com/p/old/"}

    async def prepare(self, media, caption, kind, *_):
        type(self).seen = ([path.read_bytes() for path in media], caption, kind)

    async def verify(self, *_):
        if self.fail == "verify":
            raise PublishError("ACCOUNT_MISMATCH", "Wrong account")

    async def submit(self):
        type(self).clicks += 1
        if self.fail == "submit":
            raise TimeoutError()

    async def confirm(self, *_):
        if self.fail == "confirm":
            raise PublishError("POST_URL_UNVERIFIED", "URL unavailable", 4)
        self.post_url = "https://www.instagram.com/reel/new/"
        return self.post_url

    async def screenshot(self):
        return None


@pytest.fixture
def state(tmp_path, monkeypatch):
    import instagram_automation.service as service
    monkeypatch.setattr(service, "validate_media", lambda path, kind: ".mp4" if kind == "reel" else ".jpg")
    Adapter.clicks, Adapter.fail, Adapter.seen = 0, None, None
    state = State(tmp_path / "state")
    state.bind("caribbean", "caribbeanawesome", "Caribbean Awesome")
    return state


@pytest.mark.parametrize("flags,code", [([], 2), (["publish"], 2), (["--help"], 0),
                                       (["publish", "--help"], 0), (["accounts", "list"], 0)])
def test_json_stdout(flags, code, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("IGPUB_STATE_DIR", str(tmp_path / "state"))
    assert main(flags) == code
    output = capsys.readouterr().out
    assert len(output.splitlines()) == 1
    assert isinstance(json.loads(output), dict)


@pytest.mark.parametrize("schema_version", ["1.0.0", "2.0.0"])
def test_caption_preserved_and_complete_metadata_required(tmp_path, schema_version):
    from metadata_automation import generate
    caption = tmp_path / "caption.txt"
    caption.write_bytes(b"  Exact\r\nCaption #tag\n")
    assert read_caption(caption_file=str(caption)) == "  Exact\r\nCaption #tag\n"
    envelope = generate({"language": "en", "title": "A title", "summary": "Exact caption", "keywords": ["tag"]},
                        schema_version=schema_version)
    envelope["platforms"]["instagram"]["caption"] = "  Exact\r\nCaption #tag\n"
    metadata = tmp_path / "metadata.json"
    metadata.write_text(json.dumps(envelope))
    assert read_caption(metadata_file=str(metadata)) == "  Exact\r\nCaption #tag\n"
    for mutate in (lambda data: data["platforms"].pop("pinterest" if schema_version == "2.0.0" else "youtube"),
                   lambda data: data.update(schema_version="3.0.0"),
                   lambda data: data.update(privacy="public")):
        invalid = deepcopy(envelope)
        mutate(invalid)
        metadata.write_text(json.dumps(invalid))
        with pytest.raises(PublishError, match="invalid"):
            read_caption(metadata_file=str(metadata))


def test_snapshot_and_order(tmp_path):
    source, copy = tmp_path / "source", tmp_path / "copy"
    source.write_bytes(b"uploaded bytes")
    digest = snapshot(str(source), copy)
    source.write_bytes(b"later content")
    assert copy.read_bytes() == b"uploaded bytes" and len(digest) == 64
    assert copy.stat().st_mode & 0o777 == 0o600
    with pytest.raises(PublishError):
        normalized(args(tmp_path, kind="carousel", media=[str(source)]))


def test_lock_across_processes(state):
    code = "from pathlib import Path; from instagram_automation.state import State; from instagram_automation.errors import PublishError\ns=State(Path(__import__('sys').argv[1]))\ntry:\n with s.lock('caribbean'): raise SystemExit(99)\nexcept PublishError as e: raise SystemExit(e.exit_code)"
    with state.lock("caribbean"):
        child = subprocess.run([sys.executable, "-c", code, str(state.root)], capture_output=True)
    assert child.returncode == 3, child.stderr.decode()


def test_alias_cannot_rebind_or_bypass_journal(state):
    with pytest.raises(PublishError):
        state.bind("caribbean", "other", "Caribbean Awesome")
    with pytest.raises(PublishError):
        state.bind("another", "caribbeanawesome", "Caribbean Awesome")


async def test_cached_success_without_files_or_browser(state, tmp_path):
    request = args(tmp_path)
    output, code = await publish(state, request, fake_session, Adapter)
    assert code == 0 and output["status"] == "published" and Adapter.clicks == 1
    Path(request.video).unlink()
    async def forbidden(*_):
        raise AssertionError("Cached result must not launch a browser")
    second, code = await publish(state, request, forbidden, Adapter)
    assert second == output and code == 0 and Adapter.clicks == 1


@pytest.mark.parametrize("failure,status,code", [(None, "published", 0), ("confirm", "uncertain", 4)])
async def test_cleanup_failure_preserves_submitted_result(state, tmp_path, monkeypatch, capsys, failure, status, code):
    from instagram_automation import service
    original = service.TemporaryDirectory
    class FailedCleanup:
        def __init__(self, **kwargs):
            self.inner = original(**kwargs)
            self.name = self.inner.name
        def cleanup(self):
            self.inner.cleanup()
            raise OSError("Simulated cleanup failure after Share")
    monkeypatch.setattr(service, "TemporaryDirectory", FailedCleanup)
    Adapter.fail = failure
    request = args(tmp_path)
    output, exit_code = await publish(state, request, fake_session, Adapter)
    assert exit_code == code and output["status"] == status
    assert state.get(request.account, request.request_id)["status"] == status
    assert "cleanup failed" in capsys.readouterr().err
    repeated, exit_code = await publish(state, request, fake_session, Adapter)
    assert repeated == output and exit_code == code and Adapter.clicks == 1


@pytest.mark.parametrize("failure", ["submit", "confirm"])
async def test_uncertain_never_clicks_twice(state, tmp_path, failure):
    Adapter.fail = failure
    request = args(tmp_path)
    output, code = await publish(state, request, fake_session, Adapter)
    assert code == 4 and output["status"] == "uncertain" and Adapter.clicks == 1
    Path(request.video).unlink()
    again, code = await publish(state, request, fake_session, Adapter)
    assert again == output and code == 4 and Adapter.clicks == 1


async def test_failed_retry_requires_same_bytes(state, tmp_path):
    Adapter.fail = "verify"
    request = args(tmp_path)
    output, code = await publish(state, request, fake_session, Adapter)
    assert code == 2 and output["status"] == "failed" and Adapter.clicks == 0
    Path(request.video).write_bytes(b"changed")
    with pytest.raises(PublishError, match="different caption or media"):
        await publish(state, request, fake_session, Adapter)
    Path(request.video).write_bytes(b"first video")
    Adapter.fail = None
    output, code = await publish(state, request, fake_session, Adapter)
    assert code == 0 and Adapter.clicks == 1


async def test_argument_conflict_before_browser(state, tmp_path):
    request = args(tmp_path)
    await publish(state, request, fake_session, Adapter)
    request.caption += "changed"
    with pytest.raises(PublishError, match="different arguments"):
        await publish(state, request, fake_session, Adapter)
    assert Adapter.clicks == 1


@pytest.mark.parametrize("changes", [{"ai_generated": True}, {"cover": "different.jpg"}])
async def test_reel_options_are_part_of_request_arguments(state, tmp_path, changes):
    request = args(tmp_path)
    await publish(state, request, fake_session, Adapter)
    for field, value in changes.items():
        setattr(request, field, value)
    with pytest.raises(PublishError, match="different arguments"):
        await publish(state, request, fake_session, Adapter)
    assert Adapter.clicks == 1


async def test_changed_cover_bytes_conflict_before_retry(state, tmp_path):
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"first cover")
    request = args(tmp_path, cover=str(cover))
    Adapter.fail = "verify"
    await publish(state, request, fake_session, Adapter)
    cover.write_bytes(b"changed cover")
    with pytest.raises(PublishError, match="different caption or media bytes"):
        await publish(state, request, fake_session, Adapter)
    assert Adapter.clicks == 0


@pytest.mark.parametrize("kind", ["photo", "carousel"])
def test_non_reels_reject_cover_and_ai_options(tmp_path, kind):
    with pytest.raises(PublishError, match="apply only to Reels"):
        normalized(args(tmp_path, kind=kind, cover="cover.jpg"))
    with pytest.raises(PublishError, match="apply only to Reels"):
        normalized(args(tmp_path, kind=kind, ai_generated=True))


async def test_carousel_snapshots_in_requested_order(state, tmp_path):
    first, second = tmp_path / "first.jpg", tmp_path / "second.jpg"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    request = args(tmp_path, kind="carousel", video=None, media=[str(second), str(first)])
    await publish(state, request, fake_session, Adapter)
    assert Adapter.seen[0] == [b"second", b"first"]
    request.media.reverse()
    with pytest.raises(PublishError, match="different arguments"):
        await publish(state, request, fake_session, Adapter)


async def test_crash_boundary_and_read_only_status(state, tmp_path, capsys):
    request = args(tmp_path)
    state.start("caribbean", "one", normalized(request), "fingerprint", {})
    state.finish("caribbean", "one", "submitting")
    code = main(["status", "--state-dir", str(state.root), "--account", "caribbean", "--request-id", "one"])
    assert code == 0 and json.loads(capsys.readouterr().out)["status"] == "uncertain"
    assert state.get("caribbean", "one")["status"] == "submitting"
    output, code = await publish(state, request, fake_session, Adapter)
    assert code == 4 and output["error"]["code"] == "INTERRUPTED_SUBMISSION"
    assert state.get("caribbean", "one")["status"] == "uncertain" and Adapter.clicks == 0


@pytest.mark.parametrize("url", ["http://example.com/video.mp4", "https://u:p@example.com/video", "https://example.com:444/video"])
def test_bad_remote_source(url):
    with pytest.raises(PublishError):
        source_name(url)


@pytest.mark.parametrize("address", ["127.0.0.1", "192.168.1.1", "169.254.169.254", "::1", "10.0.0.1"])
def test_local_network_download_rejected(address, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))])
    with pytest.raises(PublishError) as error:
        public_addresses("example.com")
    assert error.value.code == "UNSAFE_MEDIA_URL"


def test_only_concrete_instagram_post_urls():
    assert post_url("/reel/Real123/?igsh=tracking") == "https://www.instagram.com/reel/Real123/"
    assert post_url("https://example.org/p/Real123/") is None
    assert post_url("/caribbeanawesome/") is None
