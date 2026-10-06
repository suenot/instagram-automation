import argparse
import asyncio
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import sys

from .errors import PublishError
from .service import accept_ui, login, publish, reconcile
from .browser import post_url
from .state import State, account_name, request_id, result


class HelpRequested(Exception):
    def __init__(self, text):
        self.text = text


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise PublishError("INVALID_ARGUMENTS", message)

    def print_help(self, file=None):
        raise HelpRequested(self.format_help())


def timeout_seconds(value):
    try:
        number = int(value)
        if number < 1:
            raise ValueError()
        return number
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a positive number of seconds") from exc


def parser():
    root = Parser(prog="igpub", description="Publish Instagram media through persistent Camoufox.")
    commands = root.add_subparsers(dest="command", required=True)
    common = Parser(add_help=False)
    common.add_argument("--state-dir", type=Path, default=Path(os.environ.get("IGPUB_STATE_DIR", Path.cwd() / ".local/igpub")))
    common.add_argument("--account", required=True)
    runtime = Parser(add_help=False)
    runtime.add_argument("--timeout-seconds", type=timeout_seconds, default=1800)
    runtime.add_argument("--headless", action="store_true")
    log = commands.add_parser("login", parents=[common])
    log.add_argument("--timeout-seconds", type=timeout_seconds, default=1800)
    log.add_argument("--import-firefox", type=Path, help="Import only Instagram cookies into an unused Camoufox profile, then verify the fixed account.")
    for command in ("publish", "accept-ui"):
        pub = commands.add_parser(command, parents=[common, runtime])
        if command == "publish":
            pub.add_argument("--request-id", required=True)
        pub.add_argument("--kind", choices=["reel", "photo", "carousel"])
        media = pub.add_mutually_exclusive_group(required=True)
        media.add_argument("--media", action="append")
        media.add_argument("--video", help="Alias for one Reel media source.")
        captions = pub.add_mutually_exclusive_group(required=True)
        captions.add_argument("--caption")
        captions.add_argument("--caption-file")
        captions.add_argument("--metadata-file")
        pub.add_argument("--cover", help="Local JPEG/PNG cover for a Reel.")
        pub.add_argument("--ai-generated", action="store_true", help="Enable and verify the editor's Add AI label.")
    status = commands.add_parser("status", parents=[common])
    status.add_argument("--request-id", required=True)
    rec = commands.add_parser("reconcile", parents=[common, runtime])
    rec.add_argument("--request-id", required=True)
    rec.add_argument("--post-url", required=True)
    rec.add_argument("--confirm-request-content", action="store_true", required=True,
                     help="Confirm you manually inspected the exact uploaded media and caption at this URL.")
    accounts = commands.add_parser("accounts")
    actions = accounts.add_subparsers(dest="action", required=True)
    listing = actions.add_parser("list")
    listing.add_argument("--state-dir", type=Path, default=common.get_default("state_dir"))
    debug = commands.add_parser("debug-ui")
    debug.add_argument("--state-dir", type=Path, default=common.get_default("state_dir"))
    debug.add_argument("--port", type=int, default=8765)
    return root


def failure(args, error, raw):
    payload = {"platform": "instagram", "status": "failed", "error": error.json()}
    command = args.command if args else (raw[0] if raw else None)
    if command in {"publish", "status", "reconcile"}:
        payload.update({"account": getattr(args, "account", None), "request_id": getattr(args, "request_id", None), "post_url": None})
    elif command in {"login", "accept-ui"}:
        payload["account"] = getattr(args, "account", None)
    return payload


def main(argv=None):
    raw = list(sys.argv[1:] if argv is None else argv)
    args, state = None, None
    try:
        # Includes library diagnostics. Only the final JSON line writes to stdout.
        with redirect_stdout(sys.stderr):
            args = parser().parse_args(raw)
            if hasattr(args, "account"):
                account_name(args.account)
            if hasattr(args, "request_id"):
                request_id(args.request_id)
            if args.command in {"publish", "accept-ui"}:
                if args.video and args.kind not in {None, "reel"}:
                    raise PublishError("INVALID_ARGUMENTS", "--video can only publish a Reel.")
                if args.media and not args.kind:
                    raise PublishError("INVALID_ARGUMENTS", "--media requires an explicit --kind.")
                if (args.cover or args.ai_generated) and args.kind not in {None, "reel"}:
                    raise PublishError("INVALID_ARGUMENTS", "Cover and AI label options apply only to Reels.")
            if args.command == "reconcile":
                args.post_url = post_url(args.post_url)
                if args.post_url is None:
                    raise PublishError("INVALID_POST_URL", "Expected an HTTPS Instagram post or Reel URL.")
            old_umask = os.umask(0o077)
            try:
                state = State(args.state_dir.expanduser().absolute())
                if args.command == "accounts":
                    payload, code = {"platform": "instagram", "status": "ok", "accounts": state.accounts(), "error": None}, 0
                elif args.command == "status":
                    row = state.get(args.account, args.request_id)
                    if row is None:
                        raise PublishError("REQUEST_NOT_FOUND", "No journal entry for this account and request ID.")
                    # Read-only status reports a crashed boundary as uncertain without writing.
                    if row["status"] == "submitting":
                        row["status"] = "uncertain"
                        row["error"] = json.dumps({"code": "INTERRUPTED_SUBMISSION", "message": "Submission was interrupted; inspect this post manually."})
                    payload, code = result(row), 0
                elif args.command == "debug-ui":
                    from .debug_ui import serve
                    payload, code = serve(state, args.port)
                else:
                    handler = {"login": login, "publish": publish, "reconcile": reconcile, "accept-ui": accept_ui}[args.command]
                    payload, code = asyncio.run(handler(state, args))
            finally:
                os.umask(old_umask)
    except HelpRequested as exc:
        payload, code = {"platform": "instagram", "status": "ok", "help": exc.text, "error": None}, 0
    except PublishError as exc:
        payload, code = failure(args, exc, raw), exc.exit_code
    except KeyboardInterrupt:
        error = PublishError("INTERRUPTED", "Command interrupted; read status before trying to publish again.", 3)
        payload, code = failure(args, error, raw), 3
        if args and state and args.command == "publish":
            try:
                row = state.get(args.account, args.request_id)
                if row and row["status"] in {"submitting", "uncertain"}:
                    payload, code = result(state.recover(row)), 4
            except Exception:
                payload, code = failure(args, PublishError("INTERRUPTED_UNCERTAIN", "Interrupted with unavailable journal; inspect this request manually.", 4), raw), 4
                payload["status"] = "uncertain"
    except Exception:
        payload, code = failure(args, PublishError("TECHNICAL_FAILURE", "Local state or browser operation failed.", 3), raw), 3
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
