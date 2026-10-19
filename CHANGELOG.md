# Changelog

All notable changes to this project are documented here. The format follows Keep a Changelog, and versions follow Semantic Versioning.

## [Unreleased]

### Fixed

- Type the caption at a pace a hand produces, in both editors. The Lexical path used `press_sequentially(delay=5)`, a metronome, and the plain path used `fill`, which hands the field its whole value with no keystroke behind it.

### Added

- `human` module for input that leaves the trace a person leaves: an eased approach to a control, a pause before pressing, typing with a varying interval, insertion for what a keyboard cannot produce, and a real clipboard paste. The paste is the default for text a person would not retype from memory (long, multi-line or outside ASCII), because nobody composes a caption in an upload form; short single-line ASCII is typed. `allow_clipboard=False` turns the paste off. The clipboard is global, so it is re-read immediately before the paste key and the paste is abandoned if it is no longer ours. Kept in step with the copies in the other publishers, which are standalone repositories sharing no package. The measurements behind it are in `docs-site/@docs/guides/what-a-page-sees-when-we-type.md`.
- Test that fails the build if any module in the package dispatches a browser event or puts text in without keystrokes through `execCommand`, `fill`, `insert_text` or `press_sequentially`.

## [0.2.1] - 2026-10-08

### Fixed

- Wait for video sharing under the command deadline instead of closing after two minutes.
- Recover the new Reel URL from this Share's successful configure response only when one media object contains the exact author, caption, video type and shortcode. Keep private diagnostics limited to endpoint path, status and public result URLs.
- Verify a redirect to the new Reels viewer through the same shortcode's single-post view, rejecting changed shortcodes and unrelated recommendations.
- Read the observed nested author link beside the original caption without borrowing text from comments.

## [0.2.0] - 2026-10-07

### Added

- Site-scoped Firefox session import into an unused persistent Camoufox profile, with read-only in-memory cookies, exact account verification and cleanup after failed import.
- `accept-ui` for real pre-submit verification without a request journal entry or Share.
- Mandatory Public account checks before preparation and final submission.

### Fixed

- Recognize the live own-profile sidebar and Edit profile controls while excluding feed avatars.
- Wait for asynchronous editor steps, support the actual file chooser and acknowledge only the observed Reels information dialog.
- Reject a clipped or zoomed video even when its visible box retains 9:16.
- Verify exact cover bytes in the primary CSS preview and observed video/cover overlay slot; matching thumbnails and ambiguous equal-size media cannot prove cover selection.
- Preserve Lexical captions with native key events, exact paragraph reading and safe dismissal of hashtag suggestions.
- Verify the observed primary post author and adjacent caption structure, preserving line breaks, and reject redirects away from the exact post URL.

## [0.1.1] - 2026-10-06

### Fixed

- Pin metadata-automation `0.2.0` to accept complete schema `2.0.0` envelopes with ten platforms, including Pinterest, while preserving `1.0.0` support and exact Instagram captions.

## [0.1.0] - 2026-10-06

### Added

- Reusable `igpub` CLI for ordered Reel, photo, and carousel uploads, visible login, accounts list, status, and explicit manual reconciliation.
- Exact @caribbeanawesome / Caribbean Awesome binding, private persistent Camoufox profiles, stable fingerprints, and immediate profile locks.
- Durable SQLite request journal with media snapshots, strict metadata envelope validation, cached results, crash recovery, and one Share invocation.
- Bounded HTTPS downloads with public-IP validation, private diagnostics, and loopback debug form using the same publication handler.
- Pinned dependencies, uv.lock, caller examples, JSON/exit contract, and focused local verification. Live Camoufox UI and OS runtime gates remain pending.
- Explicit full-frame 9:16 Reel crop verification, optional cover snapshots and verified AI label state, included in request fingerprints.

### Fixed

- Preserve published/uncertain results when private upload cleanup fails after Share.
- Require the current Share result's personal post URL; concurrent profile posts with identical captions cannot establish publication identity.
- Bound DNS, all connection/TLS attempts, redirects and downloads to one total HTTPS deadline.
- Verify the author-owned caption field with exact whitespace and reject wrong post types, image counts, and carousel order during confirmation/reconciliation.
