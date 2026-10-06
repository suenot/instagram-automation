from unittest.mock import AsyncMock

import pytest

from instagram_automation.browser import InstagramEditor, browser_session
from instagram_automation.errors import PublishError


async def test_share_only_once_even_when_click_fails(tmp_path):
    adapter = InstagramEditor(object(), tmp_path / "diagnostics")
    adapter.share = AsyncMock()
    adapter.share.click.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        await adapter.submit()
    with pytest.raises(PublishError) as error:
        await adapter.submit()
    assert error.value.code == "SUBMISSION_GUARD"
    adapter.share.click.assert_awaited_once()


async def test_unverified_share_cannot_be_invoked(tmp_path):
    adapter = InstagramEditor(object(), tmp_path / "diagnostics")
    with pytest.raises(PublishError):
        await adapter.submit()


async def test_persistent_context_reuses_private_json_fingerprint(tmp_path, monkeypatch):
    from camoufox import async_api, fingerprints
    import json
    captured = []
    class Session:
        def __init__(self, **options):
            captured.append(options)
        async def __aenter__(self):
            return object()
        async def __aexit__(self, *_):
            pass
    generator = lambda **_kwargs: {"navigator": {"userAgent": "Firefox test"}, "screen": {"width": 1000}}
    monkeypatch.setattr(fingerprints, "generate_fingerprint", generator)
    monkeypatch.setattr(async_api, "AsyncCamoufox", Session)
    profile = tmp_path / "account" / "profile"
    profile.mkdir(parents=True)
    async with browser_session(profile, True):
        pass
    monkeypatch.setattr(fingerprints, "generate_fingerprint", lambda **_kwargs: pytest.fail("Fingerprint must be reused"))
    async with browser_session(profile, True):
        pass
    assert captured[0]["persistent_context"] is True
    assert captured[0]["user_data_dir"] == str(profile)
    assert captured[0]["fingerprint"] == captured[1]["fingerprint"]
    path = profile.parent / "fingerprint.json"
    assert json.loads(path.read_text()) == captured[0]["fingerprint"]
    assert path.stat().st_mode & 0o777 == 0o600
