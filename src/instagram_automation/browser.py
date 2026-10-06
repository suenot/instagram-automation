"""Conservative Instagram web adapter. The final Share action is never retried.

The English UI selectors need live account verification before a release is
advertised as end-to-end tested. Missing or ambiguous controls fail closed.
"""

import asyncio
import base64
import hashlib
from contextlib import asynccontextmanager
import os
import json
from pathlib import Path
import re
import sys
from urllib.parse import urljoin, urlsplit

from .errors import PublishError
from .state import private_dir

HOME = "https://www.instagram.com/"
EXPECTED_HANDLE = "caribbeanawesome"
EXPECTED_DISPLAY_NAME = "Caribbean Awesome"
POST_PATH = re.compile(r"^/(reel|p)/([A-Za-z0-9_-]+)/?$")
CAPTION_LABEL = re.compile(r"^(Write a caption\.\.\.|Add a caption(?:\.\.\.)?|Description)$")


@asynccontextmanager
async def browser_session(profile: Path, headless=False):
    from camoufox.fingerprints import generate_fingerprint
    from camoufox.async_api import AsyncCamoufox

    fingerprint_path = profile.parent / "fingerprint.json"
    if fingerprint_path.exists():
        fingerprint = json.loads(fingerprint_path.read_text(encoding="utf-8"))
        if not isinstance(fingerprint, dict):
            raise PublishError("INVALID_PROFILE", "The saved Camoufox fingerprint is invalid; inspect the private profile.", 3)
    else:
        fingerprint = generate_fingerprint(os="macos" if sys.platform == "darwin" else "linux")
        with fingerprint_path.open("w", encoding="utf-8") as stream:
            fingerprint_path.chmod(0o600)
            json.dump(fingerprint, stream)
    mode = "virtual" if headless and sys.platform.startswith("linux") else headless
    executable = os.environ.get("CAMOUFOX_EXECUTABLE_PATH")
    launch_options = {"executable_path": executable} if executable else {}
    try:
        async with AsyncCamoufox(persistent_context=True, user_data_dir=str(profile), fingerprint=fingerprint,
                                headless=mode, humanize=1.0, locale="en-US", block_images=False,
                                **launch_options) as context:
            yield context
    finally:
        for parent, _, files in os.walk(profile):
            Path(parent).chmod(0o700)
            for name in files:
                path = Path(parent) / name
                if not path.is_symlink():
                    path.chmod(0o600)


async def one(locator, description):
    count = await locator.count()
    if count != 1:
        raise PublishError("UI_CHANGED", f"Cannot uniquely locate {description}; found {count} controls.", 3)
    return locator


def post_url(value):
    parsed = urlsplit(urljoin(HOME, value))
    if parsed.scheme != "https" or parsed.hostname not in {"instagram.com", "www.instagram.com"} or not POST_PATH.fullmatch(parsed.path):
        return None
    return HOME.rstrip("/") + parsed.path.rstrip("/") + "/"


async def identity(context):
    """The authenticated navigation supplies the own profile, never a search result."""
    page = await context.new_page()
    try:
        await page.goto(HOME, wait_until="domcontentloaded")
        if any(part in page.url for part in ("/accounts/login", "/challenge/", "/checkpoint/")):
            raise PublishError("LOGIN_REQUIRED", "Finish visible login and security checks, then retry.")
        navigation = page.get_by_role("link", name="Profile", exact=True).filter(visible=True)
        try:
            await navigation.first.wait_for(state="visible", timeout=30000)
        except Exception as exc:
            raise PublishError("LOGIN_REQUIRED", "Authenticated Profile navigation is unavailable; run visible login.") from exc
        href = await (await one(navigation, "authenticated Profile navigation")).get_attribute("href")
        parsed = urlsplit(urljoin(HOME, href or ""))
        match = re.fullmatch(r"/([A-Za-z0-9._]+)/?", parsed.path)
        if parsed.hostname not in {"instagram.com", "www.instagram.com"} or not match:
            raise PublishError("ACCOUNT_UNVERIFIED", "Profile navigation did not identify the current Instagram account.")
        handle = match.group(1).lower()
        await page.goto(HOME + handle + "/", wait_until="domcontentloaded")
        header = await one(page.locator("main header"), "own profile header")
        await (await one(header.get_by_role("heading", name=handle, exact=True), "profile username")).wait_for(state="visible")
        display = header.get_by_text(EXPECTED_DISPLAY_NAME, exact=True)
        if handle != EXPECTED_HANDLE or await display.count() != 1 or not await display.is_visible():
            raise PublishError("ACCOUNT_MISMATCH", "Current account must be @caribbeanawesome, displayed as Caribbean Awesome.")
        return handle, EXPECTED_DISPLAY_NAME
    finally:
        await page.close()


async def verify_account(context, account):
    handle, display = await identity(context)
    if handle != account["handle"] or display != account["display_name"]:
        raise PublishError("ACCOUNT_MISMATCH", "Current Instagram account differs from the saved binding; run visible login.")


class InstagramEditor:
    def __init__(self, context, diagnostics: Path):
        self.context = context
        self.diagnostics = private_dir(diagnostics)
        self.page = None
        self.post_url = None
        self.share = None
        self.submitted = False
        self.crop_verified = False
        self.cover_hash = None

    async def screenshot(self):
        if self.page and not self.page.is_closed():
            path = self.diagnostics / "last-failure.png"
            try:
                await self.page.screenshot(path=str(path), full_page=False, timeout=5000)
                path.chmod(0o600)
                return str(path)
            except Exception:
                pass
        return None

    async def existing_urls(self, account):
        page = await self.context.new_page()
        try:
            await page.goto(account["profile_url"], wait_until="domcontentloaded")
            header = page.locator("main header")
            await header.wait_for(state="visible", timeout=30000)
            urls = await page.locator('main a[href*="/reel/"], main a[href*="/p/"]').evaluate_all("links => links.map(link => link.href)")
            return {url for raw in urls if (url := post_url(raw))}
        finally:
            await page.close()

    async def open_editor(self, account):
        await verify_account(self.context, account)
        self.page = await self.context.new_page()
        await self.page.goto(HOME, wait_until="domcontentloaded")
        create = self.page.get_by_role("link", name=re.compile(r"^(Create|New post)$")).or_(
            self.page.get_by_role("button", name=re.compile(r"^(Create|New post)$"))).filter(visible=True)
        await (await one(create, "Create navigation")).click()
        await self.page.get_by_role("dialog").wait_for(state="visible", timeout=30000)

    async def caption_editor(self):
        dialog = await one(self.page.get_by_role("dialog").filter(visible=True), "post editor dialog")
        editor = dialog.get_by_role("textbox", name=CAPTION_LABEL)
        return await one(editor, "caption editor")

    async def vertical_preview(self, dialog):
        video = await one(dialog.locator("video"), "Reel preview")
        size = await video.evaluate("""async video => {
            if (!video.videoWidth) await new Promise((resolve, reject) => {
                const timer = setTimeout(() => reject(Error('Video metadata unavailable')), 10000);
                video.addEventListener('loadedmetadata', () => {clearTimeout(timer); resolve()}, {once:true});
            });
            let rect = video.getBoundingClientRect();
            let left=rect.left, right=rect.right, top=rect.top, bottom=rect.bottom;
            for (let parent=video.parentElement; parent; parent=parent.parentElement) {
                const style=getComputedStyle(parent), box=parent.getBoundingClientRect();
                if (/hidden|clip/.test(style.overflowX)) {left=Math.max(left,box.left);right=Math.min(right,box.right)}
                if (/hidden|clip/.test(style.overflowY)) {top=Math.max(top,box.top);bottom=Math.min(bottom,box.bottom)}
            }
            return {width:video.videoWidth,height:video.videoHeight,visibleWidth:right-left,visibleHeight:bottom-top};
        }""")
        if (size["width"] * 16 != size["height"] * 9 or size["visibleHeight"] <= 0
                or abs(size["visibleWidth"] / size["visibleHeight"] - 9 / 16) > 0.015):
            raise PublishError("CROP_UNVERIFIED", "The complete vertical 9:16 Reel frame is not visible in the preview.", 3)

    async def select_crop(self, dialog):
        control = await one(dialog.get_by_role("button", name=re.compile(r"^(Select crop|Crop)$")).filter(visible=True), "Crop control")
        await control.click()
        label = re.compile(r"^9:16(?: Crop portrait icon)?$")
        choices = dialog.get_by_role("button", name=label).or_(
            dialog.get_by_role("radio", name=label)).filter(visible=True)
        choice = await one(choices, "9:16 crop option")
        await choice.click()
        # The rendered frame, including clipping ancestors, must independently
        # prove the crop; an undocumented aria state is never guessed.
        await self.vertical_preview(dialog)
        self.crop_verified = True

    async def ai_control(self, dialog):
        controls = dialog.get_by_role("switch", name="Add AI label", exact=True).or_(
            dialog.get_by_role("checkbox", name="Add AI label", exact=True))
        if await controls.count() == 0:
            label = await one(dialog.get_by_text("Add AI label", exact=True), "Add AI label text")
            container = label.locator("xpath=ancestor::*[.//*[@role='switch'] or .//input[@type='checkbox']][1]")
            controls = container.get_by_role("switch").or_(container.get_by_role("checkbox"))
        return await one(controls, "Add AI label control")

    async def ai_enabled(self, control):
        if await control.get_attribute("type") == "checkbox":
            return await control.is_checked()
        value = await control.get_attribute("aria-checked")
        if value not in {"true", "false"}:
            raise PublishError("AI_LABEL_UNVERIFIED", "The Add AI label state is not readable.", 3)
        return value == "true"

    async def set_ai_label(self, dialog, desired):
        control = await self.ai_control(dialog)
        if await self.ai_enabled(control) != desired:
            await control.click()
        if await self.ai_enabled(control) != desired:
            raise PublishError("AI_LABEL_UNVERIFIED", "The Add AI label control did not retain the requested state.", 3)

    async def select_cover(self, dialog, cover):
        select = await one(dialog.get_by_role("button", name="Select from computer", exact=True).filter(visible=True), "Cover photo file selection")
        if await dialog.get_by_text("Cover photo", exact=True).count() != 1:
            raise PublishError("COVER_UNVERIFIED", "The current Edit screen does not identify Cover photo.", 3)
        async with self.page.expect_file_chooser() as chooser:
            await select.click()
        await (await chooser.value).set_files(str(cover))
        self.cover_hash = hashlib.sha256(cover.read_bytes()).hexdigest()
        await self.verify_cover_preview(dialog)

    async def verify_cover_preview(self, dialog):
        previews = dialog.locator('img[src^="blob:"], video[poster^="blob:"]').filter(visible=True)
        await previews.first.wait_for(state="visible", timeout=10000)
        preview = await one(previews, "uploaded cover preview")
        encoded = await preview.evaluate("""async image => {
            if (image.tagName==='IMG' && (!image.complete || !image.naturalWidth)) await new Promise((resolve,reject) => {
                image.addEventListener('load',resolve,{once:true});image.addEventListener('error',reject,{once:true});
            });
            const blob=await (await fetch(image.tagName==='VIDEO' ? image.poster : image.src)).blob();
            return await new Promise((resolve,reject) => {
                const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);
                reader.onerror=reject;reader.readAsDataURL(blob);
            });
        }""")
        if not isinstance(encoded, str) or len(encoded) > 28 * 1024 ** 2:
            raise PublishError("COVER_UNVERIFIED", "Cover preview bytes exceed the verification limit.", 3)
        observed = hashlib.sha256(base64.b64decode(encoded, validate=True)).hexdigest()
        if observed != self.cover_hash:
            raise PublishError("COVER_UNVERIFIED", "The selected cover preview does not match the requested cover bytes.", 3)

    async def caption_text(self):
        editor = await self.caption_editor()
        tag = await editor.evaluate("element => element.tagName.toLowerCase()")
        return await editor.input_value() if tag in {"textarea", "input"} else await editor.inner_text()

    async def prepare(self, media, caption, kind, cover=None, ai_generated=False):
        dialog = await one(self.page.get_by_role("dialog").filter(visible=True), "post editor dialog")
        control = await one(dialog.locator('input[type="file"]'), "media upload input")
        if len(media) > 1 and await control.get_attribute("multiple") is None:
            raise PublishError("UI_CHANGED", "The current editor does not provide a verified carousel upload input.", 3)
        await control.set_input_files([str(path) for path in media])
        filenames = await control.evaluate("element => Array.from(element.files).map(file => file.name)")
        if filenames != [path.name for path in media]:
            raise PublishError("MEDIA_ORDER_UNVERIFIED", "Upload input does not contain every item in the requested order.", 3)
        if kind == "reel":
            await self.select_crop(dialog)
        # Crop and Edit each have an unambiguous Next action. Never bypass an unknown step.
        for _ in range(3):
            if await dialog.get_by_role("textbox", name=CAPTION_LABEL).count() == 1:
                break
            if cover and await dialog.get_by_text("Cover photo", exact=True).count() == 1:
                await self.select_cover(dialog, cover)
            next_button = dialog.get_by_role("button", name="Next", exact=True).filter(visible=True)
            await next_button.first.wait_for(state="visible", timeout=30000)
            await (await one(next_button, "Next editor step")).click()
        await (await self.caption_editor()).fill(caption)
        if kind == "reel":
            await self.vertical_preview(dialog)
            await self.set_ai_label(dialog, ai_generated)

    async def verify(self, account, caption, kind, cover=None, ai_generated=False):
        await verify_account(self.context, account)
        if await self.caption_text() != caption:
            raise PublishError("CAPTION_MISMATCH", "The editor did not preserve the exact caption.", 3)
        dialog = await one(self.page.get_by_role("dialog").filter(visible=True), "final post editor")
        if kind == "reel":
            if not self.crop_verified:
                raise PublishError("CROP_UNVERIFIED", "9:16 crop was not verified.", 3)
            await self.vertical_preview(dialog)
            if await self.ai_enabled(await self.ai_control(dialog)) != ai_generated:
                raise PublishError("AI_LABEL_UNVERIFIED", "Final Add AI label state differs from the request.", 3)
            if cover:
                if not self.cover_hash:
                    raise PublishError("COVER_UNVERIFIED", "Requested cover was not verified.", 3)
                await self.verify_cover_preview(dialog)
        # The final editor must show the same author as the authenticated profile.
        author = dialog.get_by_text(account["handle"], exact=True)
        if await author.count() != 1 or not await author.is_visible():
            raise PublishError("ACCOUNT_UNVERIFIED", "Final editor author cannot be verified.")
        self.share = await one(dialog.get_by_role("button", name="Share", exact=True).filter(visible=True), "final Share button")
        if not await self.share.is_enabled():
            raise PublishError("EDITOR_NOT_READY", "The final Share button is disabled.", 3)

    async def submit(self):
        if self.submitted or self.share is None:
            raise PublishError("SUBMISSION_GUARD", "Final Share was not verified or was already invoked.", 4)
        self.submitted = True
        await self.share.click(timeout=30000)

    async def confirm(self, account, caption, kind, details):
        success = self.page.get_by_text("Your post has been shared.", exact=True).or_(
            self.page.get_by_text("Your reel has been shared.", exact=True)).filter(visible=True)
        await success.first.wait_for(state="visible", timeout=120000)
        if await success.count() != 1:
            raise PublishError("SUBMISSION_UNCERTAIN", "Instagram did not uniquely confirm successful sharing.", 4)
        # Only the current Share result may identify this post. A profile delta
        # could belong to another publisher, even with the same author/caption.
        dialogs = success.locator("xpath=ancestor::*[@role='dialog'][1]")
        if await dialogs.count() != 1:
            raise PublishError("POST_URL_UNVERIFIED", "The current Share result has no unique result dialog; inspect manually.", 4)
        urls = await dialogs.locator('a[href*="/reel/"], a[href*="/p/"]').evaluate_all(
            "links => links.map(link => link.href)")
        candidates = {url for raw in urls if (url := post_url(raw))}
        if len(candidates) != 1:
            raise PublishError("POST_URL_UNVERIFIED", "Sharing was confirmed but its result does not identify a unique personal post URL; inspect manually.", 4)
        candidate = candidates.pop()
        await self.verify_post(candidate, account, caption, kind, details)
        self.post_url = candidate
        return candidate

    async def author_caption(self, article, account):
        author = f'a[href="/{account["handle"]}/"]'
        entries = article.locator(f"ul li:has({author})")
        entry = await one(entries, "author's caption entry")
        fields = entry.locator('span[dir="auto"]').filter(visible=True)
        indexes = await fields.evaluate_all("""elements => elements.flatMap((element,index) =>
            element.closest('a, button, time') ? [] : [index])""")
        if len(indexes) != 1:
            raise PublishError("POST_CAPTION_UNVERIFIED", "The author's specific caption field is not uniquely readable.", 4)
        return await fields.nth(indexes[0]).evaluate(r"""element => {
            const text=node => node.nodeType===Node.TEXT_NODE ? node.nodeValue :
                node.nodeName==='BR' ? '\n' : Array.from(node.childNodes).map(text).join('');
            return text(element);
        }""")

    async def media_image(self, article, account):
        names = "|".join(re.escape(value) for value in (account["handle"], account["display_name"]))
        image = await one(article.get_by_role("img", name=re.compile(rf"^Photo by (?:{names})(?:\b|[ ,])")).filter(visible=True),
                          "published media image")
        if not await image.evaluate("image => image.complete && image.naturalWidth>0 && image.naturalHeight>0"):
            raise PublishError("POST_MEDIA_UNVERIFIED", "Published photo has not loaded.", 4)
        return image

    async def image_bytes_hash(self, page, image):
        source = await image.get_attribute("src")
        parsed = urlsplit(source or "")
        if parsed.scheme != "https" or not parsed.hostname or not any(
                parsed.hostname == domain or parsed.hostname.endswith("." + domain)
                for domain in ("cdninstagram.com", "fbcdn.net")):
            raise PublishError("POST_MEDIA_UNVERIFIED", "The image is not exposed by a verified Instagram media CDN.", 4)
        response = await page.context.request.get(source, timeout=30000)
        try:
            if not response.ok:
                raise PublishError("POST_MEDIA_UNVERIFIED", "Published image bytes could not be read.", 4)
            body = await response.body()
            if not 0 < len(body) <= 20 * 1024 ** 2:
                raise PublishError("POST_MEDIA_UNVERIFIED", "Published image bytes exceed the verification limit.", 4)
            return hashlib.sha256(body).hexdigest()
        finally:
            await response.dispose()

    async def verify_post_media(self, article, page, kind, details, manually_confirmed):
        videos = article.locator("video")
        if kind == "reel":
            if await videos.count() != 1:
                raise PublishError("POST_TYPE_MISMATCH", "Published Reel must contain one video.", 4)
            return
        if await videos.count() != 0:
            raise PublishError("POST_TYPE_MISMATCH", "Photo/image carousel cannot be verified from a video post.", 4)
        expected = details["media_sha256"]
        controls = article.get_by_role("button", name=re.compile(r"^Go to slide \d+$"))
        if kind == "photo":
            if len(expected) != 1 or await controls.count() != 0:
                raise PublishError("POST_MEDIA_COUNT_MISMATCH", "Requested photo is not one unambiguous image.", 4)
            image = await self.media_image(article, details["account_identity"])
            if not manually_confirmed and await self.image_bytes_hash(page, image) != expected[0]:
                raise PublishError("POST_MEDIA_TRANSFORMED", "Instagram transformed the image; inspect its content manually before reconciliation.", 4)
            return
        if len(expected) < 2 or await controls.count() != len(expected):
            raise PublishError("POST_MEDIA_COUNT_MISMATCH", "Carousel slide count cannot be matched to the requested images.", 4)
        for index, expected_hash in enumerate(expected, 1):
            control = await one(article.get_by_role("button", name=f"Go to slide {index}", exact=True), f"carousel slide {index}")
            await control.click()
            image = await self.media_image(article, details["account_identity"])
            if manually_confirmed:
                states = [await control.get_attribute(name) for name in ("aria-current", "aria-pressed", "aria-checked")]
                if "true" not in states:
                    raise PublishError("POST_MEDIA_ORDER_UNVERIFIED", "The selected carousel slide number is not readable.", 4)
            elif await self.image_bytes_hash(page, image) != expected_hash:
                raise PublishError("POST_MEDIA_ORDER_UNVERIFIED", "Carousel bytes/order differ or Instagram transformed them; inspect every slide before reconciliation.", 4)

    async def verify_post(self, url, account, caption, kind, details, manually_confirmed=False):
        canonical = post_url(url)
        if not canonical:
            raise PublishError("INVALID_POST_URL", "Expected a canonical HTTPS Instagram post or Reel URL.")
        if kind != "reel" and "/reel/" in canonical:
            raise PublishError("POST_TYPE_MISMATCH", "A Reel URL cannot confirm a photo or image carousel.", 4)
        page = await self.context.new_page()
        try:
            await page.goto(canonical, wait_until="domcontentloaded")
            article = await one(page.get_by_role("article"), "published post article")
            await article.wait_for(state="visible", timeout=30000)
            author = article.locator(f'header a[href="/{account["handle"]}/"]')
            if await author.count() != 1:
                raise PublishError("POST_UNVERIFIED", "Published post author is not uniquely verified.", 4)
            if await self.author_caption(article, account) != caption:
                raise PublishError("POST_UNVERIFIED", "Published post caption cannot be verified exactly.", 4)
            evidence = {**details, "account_identity": account}
            await self.verify_post_media(article, page, kind, evidence, manually_confirmed)
        finally:
            await page.close()
