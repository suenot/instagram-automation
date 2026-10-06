"""Read Instagram cookies from a consistent, read-only Firefox SQLite snapshot.

Cookie values stay in memory until Camoufox stores them in its private profile.
Never include values in exceptions, diagnostics or command output.
"""

from pathlib import Path
import sqlite3
import time

from .errors import PublishError

MAX_COOKIES = 4096
SCHEMA_VERSIONS = {15, 16, 17}
REQUIRED_COLUMNS = {
    "name", "value", "host", "path", "expiry", "isSecure", "isHttpOnly",
    "sameSite", "originAttributes",
}
SAME_SITE = {0: "None", 1: "Lax", 2: "Strict"}


def firefox_cookies(profile: Path) -> list[dict]:
    source_path = profile.expanduser().resolve() / "cookies.sqlite"
    if not source_path.is_file():
        raise PublishError("FIREFOX_PROFILE_UNREADABLE", "Firefox profile has no readable cookies.sqlite; use visible Camoufox login.")
    source = snapshot = None
    try:
        source = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True, timeout=5)
        snapshot = sqlite3.connect(":memory:")
        deadline = time.monotonic() + 15

        def progress(_status, _remaining, _total):
            if time.monotonic() > deadline:
                raise TimeoutError()

        source.backup(snapshot, pages=128, progress=progress, sleep=0.05)
        snapshot.row_factory = sqlite3.Row
        version = snapshot.execute("PRAGMA user_version").fetchone()[0]
        columns = {row[1] for row in snapshot.execute("PRAGMA table_info(moz_cookies)")}
        if version not in SCHEMA_VERSIONS or not REQUIRED_COLUMNS <= columns:
            raise PublishError("FIREFOX_SCHEMA_UNSUPPORTED", "Firefox cookie database schema has not been reviewed for this importer.")
        rows = snapshot.execute(
            "SELECT name,value,host,path,expiry,isSecure,isHttpOnly,sameSite,originAttributes "
            "FROM moz_cookies WHERE host=? OR host=? OR host LIKE ? LIMIT ?",
            ("instagram.com", ".instagram.com", "%.instagram.com", MAX_COOKIES + 1),
        ).fetchall()
        if len(rows) > MAX_COOKIES:
            raise PublishError("FIREFOX_COOKIES_UNSUPPORTED", "Firefox has too many Instagram cookies to import safely.")
        now, result = time.time(), []
        for row in rows:
            host = row["host"]
            if not isinstance(host, str) or not (host.lstrip(".").lower() == "instagram.com" or host.lstrip(".").lower().endswith(".instagram.com")):
                continue
            if row["originAttributes"] != "":
                # Never flatten Firefox container or partitioned cookie scope.
                continue
            if row["sameSite"] not in (*SAME_SITE, 256):
                raise PublishError("FIREFOX_COOKIES_UNSUPPORTED", "Firefox cookie has an unsupported SameSite attribute.")
            expiry = row["expiry"] / (1000 if version >= 16 else 1)
            if expiry <= now:
                continue
            if not isinstance(row["name"], str) or not row["name"] or not isinstance(row["value"], str) or not isinstance(row["path"], str) or not row["path"].startswith("/"):
                raise PublishError("FIREFOX_COOKIES_UNSUPPORTED", "Firefox cookie has invalid required attributes.")
            cookie = {
                "name": row["name"], "value": row["value"], "path": row["path"],
                "domain": host,
                "expires": expiry, "secure": bool(row["isSecure"]), "httpOnly": bool(row["isHttpOnly"]),
            }
            if row["sameSite"] in SAME_SITE:
                cookie["sameSite"] = SAME_SITE[row["sameSite"]]
            result.append(cookie)
        if not result:
            raise PublishError("FIREFOX_SESSION_MISSING", "Firefox profile has no live unpartitioned Instagram cookies; use visible Camoufox login.")
        return result
    except PublishError:
        raise
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise PublishError("FIREFOX_PROFILE_BUSY", "Firefox is using its cookie database; quit Firefox, then retry the import.") from None
        raise PublishError("FIREFOX_PROFILE_UNREADABLE", "Cannot read Firefox cookies; check profile access or use visible Camoufox login.") from None
    except (sqlite3.Error, OSError, TimeoutError, ValueError, TypeError):
        raise PublishError("FIREFOX_PROFILE_UNREADABLE", "The Firefox cookie snapshot failed; check profile access or use visible Camoufox login.") from None
    finally:
        if snapshot is not None:
            snapshot.close()
        if source is not None:
            source.close()
