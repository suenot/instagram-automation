"""Private local state and the durable one-click submission boundary."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from contextlib import contextmanager

from .errors import PublishError


def private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def account_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise PublishError("INVALID_ACCOUNT", "Account must contain ASCII letters, digits, _ or -.")
    return value


def request_id(value: str) -> str:
    if not value.strip():
        raise PublishError("INVALID_REQUEST_ID", "Request ID must be nonempty.")
    return value


class State:
    def __init__(self, root: Path):
        self.root = private_dir(root)
        self.db_path = root / "journal.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (
                    account TEXT PRIMARY KEY, handle TEXT NOT NULL UNIQUE, profile_url TEXT NOT NULL,
                    display_name TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS requests (
                    account TEXT NOT NULL, request_id TEXT NOT NULL,
                    arguments TEXT NOT NULL, fingerprint TEXT NOT NULL, details TEXT NOT NULL,
                    status TEXT NOT NULL, post_url TEXT, error TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (account, request_id)
                );
            """)
        self.db_path.chmod(0o600)

    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        return db

    @contextmanager
    def lock(self, account: str):
        path = private_dir(self.root / "accounts" / account_name(account)) / "profile.lock"
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise PublishError("PROFILE_BUSY", "Another process is using this account profile.", 3)
            yield
        finally:
            os.close(fd)

    def profile(self, account: str) -> Path:
        return private_dir(self.root / "accounts" / account / "profile")

    def account(self, account: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM accounts WHERE account=?", (account,)).fetchone()
        if row is None:
            raise PublishError("UNKNOWN_ACCOUNT", "Account is not bound; run visible login first.")
        return dict(row)

    def bind(self, account: str, handle: str, display_name: str):
        handle = handle.lower()
        with self.connect() as db:
            existing = db.execute("SELECT * FROM accounts WHERE account=? OR handle=?", (account, handle)).fetchall()
            if any(r["account"] != account or r["handle"] != handle or r["display_name"] != display_name for r in existing):
                raise PublishError("ACCOUNT_BINDING_CONFLICT", "Account alias or Instagram profile is already bound differently.")
            if existing:
                return
            try:
                db.execute("INSERT INTO accounts VALUES (?, ?, ?, ?)",
                           (account, handle, f"https://www.instagram.com/{handle}/", display_name))
            except sqlite3.IntegrityError as exc:
                raise PublishError("ACCOUNT_BINDING_CONFLICT", "This Instagram profile was concurrently bound to another alias.") from exc

    def accounts(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT account, handle, profile_url, display_name FROM accounts ORDER BY account")]

    def get(self, account: str, request: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM requests WHERE account=? AND request_id=?", (account, request)).fetchone()
        return dict(row) if row else None

    def start(self, account: str, request: str, arguments: dict, fingerprint: str, details: dict):
        with self.connect() as db:
            db.execute("""INSERT INTO requests (account, request_id, arguments, fingerprint, details, status)
                VALUES (?, ?, ?, ?, ?, 'preparing')
                ON CONFLICT(account, request_id) DO UPDATE SET status='preparing', error=NULL,
                updated_at=CURRENT_TIMESTAMP""", (account, request, canonical(arguments), fingerprint, canonical(details)))

    def finish(self, account: str, request: str, status: str, error=None, post_url=None):
        with self.connect() as db:
            db.execute("""UPDATE requests SET status=?, error=?, post_url=?, updated_at=CURRENT_TIMESTAMP
                WHERE account=? AND request_id=?""", (status, canonical(error) if error else None, post_url, account, request))

    def recover(self, row: dict):
        if row["status"] == "submitting":
            self.finish(row["account"], row["request_id"], "uncertain", {
                "code": "INTERRUPTED_SUBMISSION", "message": "Submission was interrupted; inspect this post manually before another request."
            }, row["post_url"])
            return self.get(row["account"], row["request_id"])
        return row

    def request_dir(self, account: str, request: str) -> Path:
        key = hashlib.sha256(request.encode()).hexdigest()
        return private_dir(self.root / "requests" / account / key)


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def result(row: dict) -> dict:
    return {"platform": "instagram", "account": row["account"], "request_id": row["request_id"],
            "status": row["status"], "post_url": row["post_url"],
            "error": json.loads(row["error"]) if row["error"] else None}
