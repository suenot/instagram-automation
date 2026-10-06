import asyncio
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from .browser import InstagramEditor, browser_session, identity, verify_account
from .errors import PublishError
from .media import read_caption, snapshot, source_name, validate_media
from .state import State, canonical, result


@contextmanager
def private_uploads(directory):
    temporary = TemporaryDirectory(prefix="upload-", dir=directory)
    try:
        yield temporary.name
    finally:
        try:
            temporary.cleanup()
        except Exception:
            # Cleanup cannot turn a committed/uncertain submission into failed.
            try:
                print("Private upload cleanup failed; retained files need manual cleanup. Publication result is unchanged.", file=sys.stderr)
            except Exception:
                pass


def normalized(args):
    media = [source_name(value) for value in (args.media or [args.video])]
    kind = args.kind or "reel"
    cover = getattr(args, "cover", None)
    ai_generated = getattr(args, "ai_generated", False)
    if kind != "reel" and (cover or ai_generated):
        raise PublishError("INVALID_ARGUMENTS", "Cover and AI label options apply only to Reels.")
    if cover and source_name(cover).startswith("https:"):
        raise PublishError("INVALID_COVER", "Cover must be a local JPEG or PNG file.")
    if (kind in {"reel", "photo"} and len(media) != 1) or (kind == "carousel" and len(media) < 2):
        raise PublishError("INVALID_MEDIA_COUNT", "Reel/photo requires one item; carousel requires at least two ordered images.")
    return {"kind": kind, "media": media, "caption": args.caption,
            "caption_file": str(Path(args.caption_file).expanduser().absolute()) if args.caption_file else None,
            "metadata_file": str(Path(args.metadata_file).expanduser().absolute()) if args.metadata_file else None,
            "cover": str(Path(cover).expanduser().absolute()) if cover else None,
            "ai_generated": ai_generated, "aspect_ratio": "9:16" if kind == "reel" else "original"}


async def publish(state: State, args, session_factory=browser_session, adapter_factory=InstagramEditor):
    with state.lock(args.account):
        account = state.account(args.account)
        arguments = normalized(args)
        row = state.get(args.account, args.request_id)
        if row:
            if row["arguments"] != canonical(arguments):
                raise PublishError("REQUEST_CONFLICT", "Request ID was already used with different arguments.")
            row = state.recover(row)
            if row["status"] in {"published", "uncertain"}:
                return result(row), 0 if row["status"] == "published" else 4
        directory = state.request_dir(args.account, args.request_id)
        with private_uploads(directory) as staging:
            paths, hashes = [], []
            # No network/browser access occurs for an already terminal request.
            for index, source in enumerate(arguments["media"]):
                path = Path(staging) / f"{index + 1:03d}.media"
                digest = await asyncio.to_thread(snapshot, source, path)
                extension = await asyncio.to_thread(validate_media, path, arguments["kind"])
                final = path.with_suffix(extension)
                path.rename(final)
                paths.append(final)
                hashes.append(digest)
            caption = read_caption(arguments["caption"], arguments["caption_file"], arguments["metadata_file"])
            cover, cover_hash = None, None
            if arguments["cover"]:
                cover = Path(staging) / "cover.media"
                cover_hash = await asyncio.to_thread(snapshot, arguments["cover"], cover)
                extension = await asyncio.to_thread(validate_media, cover, "photo")
                final = cover.with_suffix(extension)
                cover.rename(final)
                cover = final
            details = {**arguments, "caption": caption, "media_sha256": hashes, "cover_sha256": cover_hash}
            fingerprint = hashlib.sha256(canonical(details).encode()).hexdigest()
            if row and row["fingerprint"] != fingerprint:
                raise PublishError("REQUEST_CONFLICT", "Request ID was already used with different caption or media bytes.")
            state.start(args.account, args.request_id, arguments, fingerprint, details)
            adapter, submitted, screenshot = None, False, None
            try:
                async with asyncio.timeout(args.timeout_seconds):
                    async with session_factory(state.profile(args.account), args.headless) as context:
                        adapter = adapter_factory(context, directory / "diagnostics")
                        try:
                            await adapter.open_editor(account)
                            await adapter.prepare(paths, caption, arguments["kind"], cover, arguments["ai_generated"])
                            await adapter.verify(account, caption, arguments["kind"], cover, arguments["ai_generated"])
                            # A durable FULL-synchronous commit precedes the sole Share.
                            state.finish(args.account, args.request_id, "submitting")
                            submitted = True
                            await adapter.submit()
                            url = await adapter.confirm(account, caption, arguments["kind"], details)
                            state.finish(args.account, args.request_id, "published", post_url=url)
                        except Exception:
                            try:
                                screenshot = await adapter.screenshot()
                            except Exception:
                                pass
                            raise
            except Exception as exc:
                try:
                    row = state.get(args.account, args.request_id)
                    if row and row["status"] == "published":
                        return result(row), 0
                except Exception:
                    pass
                error = exc if isinstance(exc, PublishError) else PublishError(
                    "SUBMISSION_UNCERTAIN" if submitted else "TECHNICAL_FAILURE",
                    "Browser operation failed or timed out; inspect local diagnostics.", 4 if submitted else 3)
                if screenshot:
                    error = PublishError(error.code, f"{error.message} Screenshot: {screenshot}", error.exit_code)
                status = "uncertain" if submitted else "failed"
                url = adapter.post_url if adapter else None
                try:
                    state.finish(args.account, args.request_id, status, error.json(), url)
                    output = result(state.get(args.account, args.request_id))
                except Exception:
                    output = {"platform": "instagram", "account": args.account, "request_id": args.request_id,
                              "status": status, "post_url": url,
                              "error": {"code": "JOURNAL_FAILURE", "message": "Result could not be persisted; inspect this request manually."}}
                return output, 4 if submitted else error.exit_code
            return result(state.get(args.account, args.request_id)), 0


async def login(state: State, args):
    if not sys.stdin.isatty():
        raise PublishError("INTERACTIVE_LOGIN_REQUIRED", "Visible login requires an interactive terminal.")
    with state.lock(args.account):
        async with asyncio.timeout(args.timeout_seconds):
            async with browser_session(state.profile(args.account), False) as context:
                page = await context.new_page()
                await page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded")
                print("Sign in to @caribbeanawesome (Caribbean Awesome), finish 2FA, then press Enter here.", file=sys.stderr)
                loop = asyncio.get_running_loop()
                ready = loop.create_future()
                def entered():
                    if not ready.done():
                        ready.set_result(sys.stdin.readline())
                loop.add_reader(sys.stdin, entered)
                try:
                    if not await ready:
                        raise PublishError("INTERACTIVE_LOGIN_REQUIRED", "Terminal input closed before account verification.")
                finally:
                    loop.remove_reader(sys.stdin)
                handle, display = await identity(context)
                state.bind(args.account, handle, display)
        return {"platform": "instagram", "account": args.account, "status": "authenticated", "error": None}, 0


async def reconcile(state: State, args, session_factory=browser_session, adapter_factory=InstagramEditor):
    """Manual inspection plus an independent read-only check, never another Share."""
    with state.lock(args.account):
        row = state.get(args.account, args.request_id)
        if row is None:
            raise PublishError("REQUEST_NOT_FOUND", "No journal entry for this account and request ID.")
        row = state.recover(row)
        if row["status"] == "published":
            return result(row), 0
        if row["status"] != "uncertain":
            raise PublishError("RECONCILE_UNAVAILABLE", "Manual reconciliation applies only to an uncertain request.")
        account = state.account(args.account)
        details = json.loads(row["details"])
        try:
            async with asyncio.timeout(args.timeout_seconds):
                async with session_factory(state.profile(args.account), args.headless) as context:
                    await verify_account(context, account)
                    adapter = adapter_factory(context, state.request_dir(args.account, args.request_id) / "diagnostics")
                    await adapter.verify_post(args.post_url, account, details["caption"], details["kind"], details,
                                              manually_confirmed=True)
                    state.finish(args.account, args.request_id, "published", post_url=args.post_url)
        except Exception as exc:
            error = exc if isinstance(exc, PublishError) else PublishError("RECONCILE_UNCONFIRMED", "Could not verify the inspected post; no upload or Share was attempted.", 4)
            state.finish(args.account, args.request_id, "uncertain", error.json(), row["post_url"])
            return result(state.get(args.account, args.request_id)), 4
        return result(state.get(args.account, args.request_id)), 0
