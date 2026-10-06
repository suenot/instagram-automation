# instagram-automation

`igpub` is a reusable CLI for Reels, photos, and ordered image carousels through a private persistent Camoufox profile. Callers own scheduling and queues. The `(account, request_id)` journal prevents a second Share after confirmed or ambiguous submission.

**Status:** macOS acceptance passed in paired Camoufox for a site-scoped Firefox session import, exact account binding, persistent session reuse, and full Reel preparation up to enabled Share using the original 1080×1920 MP4, exact metadata caption, cover and AI label. `accept-ui` returned `accepted`, `audience=public`, `submitted=false`. A fresh cookie-free Camoufox independently verified the existing [campaign Reel](https://www.instagram.com/reel/DeKT3BduBcC/), exact author/caption and playable vertical video. No duplicate Reel was submitted. Real CLI Share/result URL recovery, photo/carousel live preparation, default browser fetch/cache and target Linux acceptance remain unverified.

Version `0.2.0` is defined in `pyproject.toml`. Changes are recorded in `CHANGELOG.md`. The reviewed [Instagram plan](https://gitlab.com/reforms-lab/root/-/blob/main/docs/publishing/instagram.md) and [shared publishing contract](https://gitlab.com/reforms-lab/root/-/blob/main/docs/publishing/contract.md) define release gates.

## Installation and login

Requires Python 3.12/3.13, [uv](https://docs.astral.sh/uv/getting-started/installation/), access to the private metadata dependency, and ffmpeg (`ffprobe`) for Reels.

```sh
# macOS
brew install uv ffmpeg
uv sync --locked
uv run camoufox fetch

# Debian/Ubuntu: install uv using its documented installer, then:
sudo apt-get install ffmpeg xvfb libgtk-3-0 libdbus-glib-1-2 libasound2
uv sync --locked
uv run camoufox fetch

uv run igpub login --account caribbean --state-dir "$PWD/.local/igpub"
# Or transfer the existing login while Firefox is closed (no interactive prompt):
uv run igpub login --account caribbean --state-dir "$PWD/.local/igpub" \
  --import-firefox "/absolute/path/to/Firefox/profile"
uv run igpub accounts list --state-dir "$PWD/.local/igpub"
```

Dependencies are locked: Camoufox `0.5.7`, Playwright `1.60.0`, and metadata-automation `0.2.0` at commit `994ecce4c904c9c1a7f5de9086ef2a9785c25efa`. Record and test the downloaded browser version with this Python dependency pair on each target OS before release. [Camoufox usage](https://camoufox.com/python/usage/) documents persistent contexts and Linux virtual display mode; controls use [Playwright locators](https://playwright.dev/python/docs/locators).

The macOS controlled smoke used the already installed paired `official/156.0.1-beta.34` executable, pinned fpgen model verified by Camoufox's own hashes, and fresh temporary profiles. Set `CAMOUFOX_EXECUTABLE_PATH` to select an already installed paired executable explicitly. This validates the wrapper, controlled fixtures and the live macOS Reel pre-submit checks. Default fetch/cache selection and Linux still need their own checks.

This campaign binds login to `@caribbeanawesome`, displayed as `Caribbean Awesome`. `--import-firefox` reads a consistent, read-only SQLite snapshot in memory and imports only live `instagram.com` cookies, preserving domain/host-only scope, path, expiry, Secure, HttpOnly and supported SameSite values. Container and partitioned cookies are skipped. Unknown Firefox schemas fail closed. Imported cookies are never printed or exported. Import requires an unused Camoufox profile; identity/import failure clears cookies and removes that new profile. Existing profiles and the journal are preserved by refusing an import over them. Subsequent publication uses the saved Camoufox session without rereading Firefox. A fresh login or 2FA needs a visible window and interactive terminal; an authorized cookie import is noninteractive. The own sidebar Profile navigation (excluding feed avatar links), Edit profile control, profile username and display name, and final editor author must be verified; missing/ambiguous controls and security challenges stop before Share. An alias cannot be rebound or duplicated for the same profile. A session expiration requires visible `login` again.

Use the same absolute `--state-dir` on every call, or set `IGPUB_STATE_DIR`. Default: `.local/igpub` in the caller's current directory. Profiles, stable fingerprint JSON, SQLite journal, uploads, and screenshots remain private local files excluded from Git. Directories use `0700`, files `0600`, and the process uses a restrictive umask. Credentials, DOM, and captions are not written to ordinary logs. Manually clear obsolete `requests/*/*/diagnostics` and `ui-input` under the state directory; preserve profiles and journal for retries.

## Publishing

```sh
uv run igpub publish --account caribbean --request-id campaign-2026-10-06-001 \
  --kind reel --media /absolute/path/reel.mp4 \
  --metadata-file /absolute/path/metadata.json --cover /absolute/path/cover.jpg \
  --ai-generated --state-dir "$PWD/.local/igpub"

# --video is an alias for one Reel source; --kind reel can be omitted.
uv run igpub publish --account caribbean --request-id campaign-2026-10-06-002 \
  --video /absolute/path/reel.mp4 --caption-file /absolute/path/caption.txt \
  --state-dir "$PWD/.local/igpub"

uv run igpub publish --account caribbean --request-id photo-001 \
  --kind photo --media https://example.org/photo.jpg --caption "Exact caption" \
  --state-dir "$PWD/.local/igpub"

uv run igpub publish --account caribbean --request-id carousel-001 \
  --kind carousel --media /absolute/path/first.jpg --media /absolute/path/second.png \
  --caption-file /absolute/path/caption.txt --state-dir "$PWD/.local/igpub"

uv run igpub status --account caribbean --request-id campaign-2026-10-06-001 \
  --state-dir "$PWD/.local/igpub"
```

Exactly one caption input is required: `--caption`, UTF-8 `--caption-file`, or complete `--metadata-file`. Metadata uses the pinned dependency's bundled schemas: version `1.0.0` requires all nine platform blocks; version `2.0.0` requires all ten, including Pinterest. The same complete `2.0.0` file is also accepted by the Facebook and TikTok CLIs using this dependency. Only `platforms.instagram.caption` reaches the editor, verbatim; hashtags are never appended again. Caption files preserve whitespace and line endings. Lexical receives native key events because Firefox DOM fill duplicates its text; its paragraph reader preserves blank lines and whitespace. The editor value must match before Share.

Every publication requires a public account. The adapter reads Settings → Account privacy and requires the actual Private account switch to expose `aria-checked=false`, before opening the editor and again during the final checks. Private or unreadable state stops before Share; metadata does not configure account privacy. Reel source media must be vertical 9:16: the adapter explicitly selects the observed 9:16 crop option and checks intrinsic video dimensions plus the actual visible frame, including clipping ancestors, before Share. A frame clipped or zoomed within another 9:16 box is rejected. A square preview is rejected. Music and tagging are not automated.

Reels additionally support local JPEG/PNG `--cover PATH` and `--ai-generated`. The cover and AI choice participate in request arguments and fingerprints; a changed cover's bytes conflict on a retry. Photo/carousel reject both options. The adapter selects the cover through Edit → Cover photo → Select from computer, then requires the largest visible media slot to expose the exact cover bytes through its image, poster or CSS background. A matching thumbnail cannot prove selection. An unreadable primary video is allowed only with the observed same-slot structure: a relative parent containing exactly a static video followed by a fully aligned absolute cover overlay, with default stacking and full opacity. Separate or ambiguous media slots fail closed. It checks that proof again before Share. Live Camoufox exposes an exact-byte CSS cover overlay; no thumbnail border or perceptual image comparison is treated as proof. The Add AI label switch must expose a real checked state; the tool selects and rereads it, including verifying off when the flag is absent. Missing or unreadable crop, cover, or AI state fails closed.

Reels require valid vertical 9:16 MP4 with H.264 video. Photos and carousel slides require verified JPEG/PNG; carousel requires at least two items and preserves source order. Media is copied privately, validated, SHA-256 hashed, and uploaded from those same copies. There is no transcoding. HTTPS uses trusted TLS on port 443, validated public addresses, at most five redirects, and at most 1 GiB. One total 120 second deadline covers DNS resolution, every IP/connect/TLS attempt, all redirects, response headers and body. A daemon DNS worker bounds a stalled resolver; a socket watchdog aborts slow-drip responses. Each redirect is checked and the connection uses the validated IP with the original TLS hostname, preventing DNS rebinding. HTTP, credentials, local network targets, empty/incomplete downloads, and invalid bytes are rejected. Instagram post URLs are not media upload sources.

`--timeout-seconds` controls browser work (positive integer, default `1800`). Media download has its own limits. Default browser mode is visible; `--headless` uses virtual Xvfb on Linux and headless mode on macOS.

The pre-submit acceptance command exercises the same upload, account, Public, crop, cover, caption, AI and enabled Share checks, then closes the editor without calling Share or creating a request journal entry:

```sh
uv run igpub accept-ui --account caribbean --video /absolute/path/reel.mp4 \
  --metadata-file /absolute/path/metadata.json --cover /absolute/path/cover.jpg \
  --ai-generated --state-dir "$PWD/.local/igpub"
```

It returns `status=accepted`, `audience=public`, `submitted=false` and a private `accepted-editor.png` only after all checks pass. A successful acceptance does not establish a publication or its resulting URL. Run it with the complete release media; small clips are useful only for control discovery.

## Results and retries

Every command, including help and argument errors, emits one JSON object on one stdout line. Library output and prompts go to stderr. Publication/status include `platform`, `account`, `request_id`, `status`, `post_url`, and `error`.

```json
{"platform":"instagram","account":"caribbean","request_id":"campaign-2026-10-06-001","status":"published","post_url":"https://www.instagram.com/reel/ACTUAL_SHORTCODE/","error":null}
```

| Exit | Publication meaning |
| --- | --- |
| `0` | Confirmed publication, including cached result. |
| `2` | Invalid input, unknown account, missing login, or account mismatch; no Share. |
| `3` | Busy profile, unavailable browser, or changed/unverified UI before Share. |
| `4` | Submission reached its durable boundary and the outcome is uncertain. |

`status` returns `0` for every found record, `2` for an absent/invalid key. Successful login/accounts list return `0`. Errors use `{code, message}`; stdout never contains traceback, HTML, or cookies.

The profile lock covers login and the entire publication session; a second process immediately fails with `PROFILE_BUSY`. Fingerprints include kind, ordered sources and media hashes, exact caption, and caption source arguments. Changed requests conflict before browser access. Failed requests before Share can retry only with identical arguments, caption, and media bytes.

`submitting` is committed durably before one final Share invocation. Exceptions, timeout, or interruption after it yield `uncertain`; crashed `submitting` records recover conservatively. Repeating a terminal `published` or `uncertain` returns the saved result without opening the browser, downloading, or rereading caption/metadata files. A terminal ID represents the original recorded result even if the source file subsequently changes or disappears. Inspect any uncertain outcome before creating another ID. Private upload cleanup runs in a guarded finally: cleanup errors only produce a safe stderr warning and cannot change a published/uncertain result or invite another Share.

Publication requires exact success confirmation, one concrete personal URL supplied by the current Share result dialog, and independent verification of its author, specific author-owned caption field and media. Caption verification preserves spaces, line breaks and trailing whitespace; text in unrelated comments cannot satisfy it, and an empty requested caption still requires an explicit readable caption field. A Reel URL cannot confirm a photo/carousel; image posts must contain no video. Photo requires one image; carousel requires the exact number of numbered slides and checks every slide in order. Automatic image confirmation requires SHA-256 equality of the actual CDN image bytes, so Instagram image processing can leave a correctly published image request `uncertain`. No perceptual heuristic is treated as exact identity. A new profile post, including one with the same author and caption, cannot identify this Share. If the current result provides no personal URL, the request remains uncertain with post_url null and needs manual reconciliation. Missing URL or unverifiable post also yields `uncertain`; no guessed shortcode is reported. After manually inspecting the exact requested media, carousel order and caption at a concrete URL, reconcile without upload or Share:

```sh
uv run igpub reconcile --account caribbean --request-id campaign-2026-10-06-001 \
  --post-url https://www.instagram.com/reel/ACTUAL_SHORTCODE/ \
  --confirm-request-content --state-dir "$PWD/.local/igpub"
```

The confirmation asserts that the operator checked the actual requested media and order; Instagram's processed media cannot necessarily be compared to original SHA-256. The tool independently verifies the bound account, URL, exact caption, media type/count and selected carousel slide numbers; unreadable state remains `uncertain`.

Call from another process:

```python
import json
import subprocess

completed = subprocess.run([
    "/absolute/path/.venv/bin/igpub", "publish", "--account", "caribbean",
    "--request-id", "campaign-2026-10-06-001", "--video", "/absolute/path/reel.mp4",
    "--metadata-file", "/absolute/path/metadata.json",
    "--state-dir", "/absolute/path/.local/igpub",
], capture_output=True, text=True)
result = json.loads(completed.stdout)
```

## Debug form and verification

```sh
uv run igpub debug-ui --state-dir "$PWD/.local/igpub" --port 8765
uv run playwright install chromium --only-shell
uv run pytest -q
```

The loopback form chooses a saved account, format, ordered local files, and caption or metadata file. It shows a generated request ID before submitting and calls the same handler/journal/lock as CLI. JSON results and the request's last screenshot are displayed without arbitrary file access. Uploads are limited to 64 MiB including JSON encoding; larger files use CLI. Host and same-origin JSON checks reject cross-site submission and DNS rebinding. The server binds only `127.0.0.1`; Ctrl+C stops it.

Local checks cover JSON, metadata schema, captions, snapshots, carousel order, process locks, alias collisions, conflicts, cover/AI fingerprints, DNS/connect deadlines, retries, crash recovery, cached outcomes, and one Share. Controlled headless browser DOM checks exercise square-crop rejection using a real 9:16 MP4, explicit 9:16 geometry, cover bytes, real AI states, exact author caption extraction and image type/count/order. The same fixtures can use the real Camoufox wrapper by setting `IGPUB_TEST_CAMOUFOX=1` and `CAMOUFOX_EXECUTABLE_PATH`; no Instagram session is used. Live macOS Reel acceptance verified the account, Public setting, explicit crop, exact cover bytes, exact multiline caption, AI state and enabled Share with complete release media. Guest post verification passed for the existing campaign Reel without cookies. Remaining gates: default macOS fetch/cache selection and target Linux runtime; live photo/carousel preparation and numbered carousel controls; an authorized future CLI Share, success confirmation and causal concrete URL recovery. Real publication requires the authorized account, actual media and caption, followed by post/URL inspection.
