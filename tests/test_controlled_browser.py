"""Headless local DOM only; no Instagram account or live session is opened."""
import base64
from io import BytesIO
import os
import subprocess

from PIL import Image
import pytest
from playwright.async_api import async_playwright

from instagram_automation.browser import InstagramEditor, browser_session
from instagram_automation.errors import PublishError

ACCOUNT = {"handle": "caribbeanawesome", "display_name": "Caribbean Awesome"}


@pytest.fixture
async def page(tmp_path):
    if os.environ.get("IGPUB_TEST_CAMOUFOX") == "1":
        profile = tmp_path / "persistent-profile"
        profile.mkdir(mode=0o700)
        async with browser_session(profile, True) as context:
            await context.route("https://fixture.test/**", lambda route: route.fulfill(status=200, content_type="text/html", body="<html></html>"))
            page = await context.new_page()
            await page.goto("https://fixture.test/")
            yield page
        return
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.route("https://fixture.test/**", lambda route: route.fulfill(status=200, content_type="text/html", body="<html></html>"))
        page = await context.new_page()
        await page.goto("https://fixture.test/")
        yield page
        await browser.close()


async def test_square_crop_is_rejected_then_explicit_9_16_verified(page, tmp_path):
    video = tmp_path / "vertical.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=90x160:d=0.2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-y", str(video)],
                   check=True, capture_output=True)
    source = "data:video/mp4;base64," + base64.b64encode(video.read_bytes()).decode()
    await page.set_content(f'''<div role=dialog>
        <button aria-label="Select crop" onclick="document.querySelector('#portrait').hidden=false">Crop</button>
        <button id=portrait hidden aria-label="9:16 Crop portrait icon" onclick="document.querySelector('#frame').style.width='225px';document.querySelector('video').style.width='225px';document.querySelector('video').style.height='400px';this.hidden=true">9:16</button>
        <div id=frame style="width:400px;height:400px;overflow:hidden"><video preload=auto src="{source}" style="width:400px;height:711px"></video></div>
        </div>''')
    adapter = InstagramEditor(page.context, tmp_path / "diagnostics")
    adapter.page = page
    dialog = page.get_by_role("dialog")
    with pytest.raises(PublishError) as error:
        await adapter.vertical_preview(dialog)
    assert error.value.code == "CROP_UNVERIFIED"
    await adapter.select_crop(dialog)
    assert adapter.crop_verified is True


async def test_ai_label_is_checked_and_unreadable_state_rejected(page, tmp_path):
    await page.set_content('''<div role=dialog><div><span>Add AI label</span>
        <button role=switch aria-checked=false onclick="this.setAttribute('aria-checked',this.getAttribute('aria-checked')==='false'?'true':'false')"></button>
        </div></div>''')
    adapter = InstagramEditor(page.context, tmp_path / "diagnostics")
    dialog = page.get_by_role("dialog")
    await adapter.set_ai_label(dialog, True)
    assert await adapter.ai_enabled(await adapter.ai_control(dialog)) is True
    await page.get_by_role("switch").evaluate("element=>element.removeAttribute('aria-checked')")
    with pytest.raises(PublishError) as error:
        await adapter.ai_enabled(await adapter.ai_control(dialog))
    assert error.value.code == "AI_LABEL_UNVERIFIED"


async def test_cover_must_match_uploaded_bytes(page, tmp_path):
    cover = tmp_path / "cover.jpg"
    Image.new("RGB", (9, 16), "red").save(cover)
    await page.set_content('''<div role=dialog><span>Cover photo</span>
        <button onclick="document.querySelector('input').click()">Select from computer</button>
        <input type=file hidden onchange="document.querySelector('img').src=window.URL.createObjectURL(this.files[0])">
        <img></div>''')
    adapter = InstagramEditor(page.context, tmp_path / "diagnostics")
    adapter.page = page
    dialog = page.get_by_role("dialog")
    await adapter.select_cover(dialog, cover)
    await adapter.verify_cover_preview(dialog)
    adapter.cover_hash = "different"
    with pytest.raises(PublishError) as error:
        await adapter.verify_cover_preview(dialog)
    assert error.value.code == "COVER_UNVERIFIED"


async def test_author_caption_preserves_whitespace_and_ignores_comments(page, tmp_path):
    await page.set_content('''<article><header><a href="/caribbeanawesome/">caribbeanawesome</a></header>
        <ul><li><a href="/caribbeanawesome/">caribbeanawesome</a><span dir=auto>  Exact<br>Caption #tag </span></li>
        <li><a href="/other/">other</a><span dir=auto>unrelated</span></li></ul>
        <p>Different global caption</p></article>''')
    adapter = InstagramEditor(page.context, tmp_path / "diagnostics")
    assert await adapter.author_caption(page.get_by_role("article"), ACCOUNT) == "  Exact\nCaption #tag "
    await page.locator('li a[href="/other/"]').evaluate("element=>element.setAttribute('href','/caribbeanawesome/')")
    with pytest.raises(PublishError):
        await adapter.author_caption(page.get_by_role("article"), ACCOUNT)


async def test_photo_carousel_type_count_and_order(page, tmp_path, monkeypatch):
    buffer = BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")
    pixel = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    await page.set_content(f'''<article><img alt="Photo by Caribbean Awesome" src="{pixel}" data-hash=first>
        <button aria-label="Go to slide 1" aria-pressed=true onclick="document.querySelector('img').dataset.hash='first';document.querySelectorAll('button').forEach(e=>e.setAttribute('aria-pressed',String(e===this)))"></button>
        <button aria-label="Go to slide 2" aria-pressed=false onclick="document.querySelector('img').dataset.hash='second';document.querySelectorAll('button').forEach(e=>e.setAttribute('aria-pressed',String(e===this)))"></button></article>''')
    adapter = InstagramEditor(page.context, tmp_path / "diagnostics")
    async def observed_hash(_page, image):
        return await image.get_attribute("data-hash")
    monkeypatch.setattr(adapter, "image_bytes_hash", observed_hash)
    details = {"media_sha256": ["first", "second"], "account_identity": ACCOUNT}
    article = page.get_by_role("article")
    await adapter.verify_post_media(article, page, "carousel", details, False)
    await adapter.verify_post_media(article, page, "carousel", details, True)
    with pytest.raises(PublishError):
        await adapter.verify_post_media(article, page, "photo", {**details, "media_sha256": ["first"]}, True)
    with pytest.raises(PublishError) as error:
        await adapter.verify_post_media(article, page, "carousel", {**details, "media_sha256": ["second", "first"]}, False)
    assert error.value.code == "POST_MEDIA_ORDER_UNVERIFIED"
    await page.locator("article").evaluate("element=>element.append(document.createElement('video'))")
    with pytest.raises(PublishError) as error:
        await adapter.verify_post_media(article, page, "carousel", details, True)
    assert error.value.code == "POST_TYPE_MISMATCH"


async def test_reel_url_cannot_reconcile_photo(tmp_path):
    adapter = InstagramEditor(object(), tmp_path / "diagnostics")
    with pytest.raises(PublishError) as error:
        await adapter.verify_post("https://www.instagram.com/reel/Example/", ACCOUNT, "", "photo", {"media_sha256": ["hash"]}, True)
    assert error.value.code == "POST_TYPE_MISMATCH"


async def test_concurrent_identical_caption_post_cannot_confirm_current_share(page, tmp_path, monkeypatch):
    await page.set_content('''<div role=dialog><span>Your reel has been shared.</span></div>
        <a href="https://www.instagram.com/reel/ConcurrentSameCaption/">Concurrent post with identical caption</a>''')
    adapter = InstagramEditor(page.context, tmp_path / "diagnostics")
    adapter.page = page
    verified = []
    async def verify(url, *_):
        verified.append(url)
    async def profile_difference(*_):
        raise AssertionError("A profile difference cannot identify the current Share")
    monkeypatch.setattr(adapter, "verify_post", verify)
    monkeypatch.setattr(adapter, "existing_urls", profile_difference)
    with pytest.raises(PublishError) as error:
        await adapter.confirm(ACCOUNT, "Identical caption", "reel", {})
    assert error.value.code == "POST_URL_UNVERIFIED" and error.value.exit_code == 4
    assert adapter.post_url is None and verified == []
    # Only the exact Share result supplies causal URL evidence.
    await page.get_by_role("dialog").evaluate("element=>element.insertAdjacentHTML('beforeend','<a href=\"https://www.instagram.com/reel/ThisShare/\">View reel</a>')")
    assert await adapter.confirm(ACCOUNT, "Identical caption", "reel", {}) == "https://www.instagram.com/reel/ThisShare/"
    assert verified == ["https://www.instagram.com/reel/ThisShare/"]
