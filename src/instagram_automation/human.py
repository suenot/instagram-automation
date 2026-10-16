"""Input that leaves the trace a person leaves.

A page sees more than the value of a field. It sees which events arrived, in
what order, how far apart, and whether the browser marked them as
user-generated. Measured on a probe page that logs every event a form can
observe, entering the same 49-character text:

    locator.fill(text)     49 characters -> 1 beforeinput, 1 input, 0 keydown
    execCommand insertText 49 characters -> 1 input, no beforeinput, 0 keydown
    locator.type(delay=55) 49 keystrokes with 0-3 ms between keyup and keydown
    fabricated paste event isTrusted: false, clipboardData not a DataTransfer
    type_text(page, text)  49 keystrokes 42-186 ms apart, 258 mousemove samples
                           on the way to the field against 27 for a bare click

Nothing here fabricates an event: every event this module causes comes from
the browser and carries isTrusted: true.

Typing is not always the human answer either. Typing "a<palm emoji>b"
produced three input events but only two keydowns: a character outside ASCII
arrives without a keystroke. That is not a flaw to hide — it is exactly what
an emoji picker produces for a real person. So `enter_text` types the ASCII
runs and inserts the rest, which is the trace a person at this keyboard
leaves.

A real clipboard paste is a closer imitation still for a long caption: it
reaches a Lexical editor's saved state exactly as typing does, and the page sees
`isTrusted: true` with a genuine `DataTransfer`, where a driver's
`insert_text` leaves `insertText` and no paste event at all — the same
signature as `fill`. It is off by default anyway, because the clipboard
belongs to whoever else is using the machine; see `paste`.
The measurements and the method behind them are written up in
`docs-site/@docs/guides/what-a-page-sees-when-we-type.md`. This file is kept
in step with the copies in the other publishers; they are standalone
repositories and do not share a package.
"""
import random
import subprocess
import sys

# A hand does not hold an interval. It produces a tight cluster of fast
# strokes with a long tail to the right, so the base is log-normal rather than
# the symmetric distribution this module started with: exp(4.75) is a 115 ms
# median, and the tail reaches past 230 ms without anything special happening.
KEY_LOGNORM = (4.75, 0.42)       # mu, sigma of ln(milliseconds)
KEY_MINIMUM_MS = 28
KEY_CEILING_MS = 900             # above this it is a pause, not a keystroke
WORD_BREAK_MS = (25, 110)        # a space ends a word and the hand regroups
SENTENCE_BREAK_MS = (220, 650)   # after . ! ? the eye goes to what comes next
LINE_BREAK_MS = (300, 900)       # a new line is a new thought
SHIFT_MS = (20, 70)              # a capital or a symbol costs a second finger
WORD_TEMPO = (0.82, 1.28)        # each word is typed at its own tempo
THINK_CHANCE = 0.035             # per character: a pause with nothing behind it
THINK_MS = (420, 1500)
TYPO_CHANCE = 0.014              # per letter: struck a neighbour, noticed, fixed
TYPO_NOTICE_MS = (110, 360)      # how long the wrong letter stands before Backspace
TYPO_RECOVER_MS = (90, 240)      # and the pause after erasing it

# Neighbours on a QWERTY keyboard: a slip lands next to the intended key, not
# on a random letter.
_NEIGHBOURS = {
    "a": "qwsz", "b": "vghn", "c": "xdfv", "d": "serfcx", "e": "wsdr",
    "f": "drtgvc", "g": "ftyhbv", "h": "gyujnb", "i": "ujko", "j": "huikmn",
    "k": "jiolm", "l": "kop", "m": "njk", "n": "bhjm", "o": "iklp",
    "p": "ol", "q": "wa", "r": "edft", "s": "awedxz", "t": "rfgy",
    "u": "yhji", "v": "cfgb", "w": "qase", "x": "zsdc", "y": "tghu",
    "z": "asx",
}
MOVE_STEPS = (22, 34)
MOVE_STEP_MS = (6, 18)
PRESS_MS = (55, 130)
AIM_MS = (90, 260)
SETTLE_MS = (250, 900)
PASTE_READ_MS = 400              # let the browser read the clipboard before restoring it

# Longer than a person would retype from memory; captions live well above it.
TYPED_LIMIT = 120

PASTE_KEY = "Meta+V" if sys.platform == "darwin" else "Control+V"
_CLIPBOARD = {"darwin": (["pbcopy"], ["pbpaste"]),
              "linux": (["wl-copy"], ["wl-paste", "--no-newline"])}


def _cursor(page):
    return getattr(page, "_human_cursor", (12.0, 12.0))


async def move_to(page, x, y, rng=random):
    """Travel to a point along an eased arc instead of teleporting to it."""
    start_x, start_y = _cursor(page)
    steps = rng.randint(*MOVE_STEPS)
    bow = rng.uniform(-0.22, 0.22)
    distance = ((x - start_x) ** 2 + (y - start_y) ** 2) ** 0.5
    for index in range(1, steps + 1):
        progress = index / steps
        eased = progress * progress * (3 - 2 * progress)
        arc = bow * (1 - (2 * progress - 1) ** 2) * distance * 0.12
        await page.mouse.move(start_x + (x - start_x) * eased,
                              start_y + (y - start_y) * eased + arc)
        await page.wait_for_timeout(rng.randint(*MOVE_STEP_MS))
    page._human_cursor = (x, y)


async def click(page, locator, rng=random):
    """Click where a hand would land: inside the control, not dead centre."""
    await locator.scroll_into_view_if_needed()
    box = await locator.bounding_box()
    if not box:
        raise ValueError("Control is not visible; cannot click it as a person would.")
    x = box["x"] + box["width"] * rng.uniform(0.35, 0.65)
    y = box["y"] + box["height"] * rng.uniform(0.35, 0.65)
    await move_to(page, x, y, rng)
    await page.wait_for_timeout(rng.randint(*AIM_MS))
    await page.mouse.down()
    await page.wait_for_timeout(rng.randint(*PRESS_MS))
    await page.mouse.up()


def _shifted(character):
    return character.isupper() or character in "~!@#$%^&*()_+{}|:\"<>?"


def intervals(text, rng=random):
    """The pause before each character, in milliseconds.

    The shape matters more than the centre. A constant interval is what reads
    as a machine, and so does a symmetric one: real intervals pile up near the
    median and trail off to the right, and they are interrupted by pauses that
    belong to the sentence rather than to the keys — after a full stop, at a
    line break, and now and then for no visible reason at all.
    """
    values = []
    tempo = rng.uniform(*WORD_TEMPO)
    previous = ""
    for character in text:
        delay = min(KEY_CEILING_MS, rng.lognormvariate(*KEY_LOGNORM)) * tempo
        if _shifted(character):
            delay += rng.randint(*SHIFT_MS)
        if character == " ":
            delay += rng.randint(*WORD_BREAK_MS)
            tempo = rng.uniform(*WORD_TEMPO)   # the next word has its own pace
        if character == "\n":
            delay += rng.randint(*LINE_BREAK_MS)
        if previous in ".!?" and character == " ":
            delay += rng.randint(*SENTENCE_BREAK_MS)
        if rng.random() < THINK_CHANCE:
            delay += rng.randint(*THINK_MS)
        values.append(max(KEY_MINIMUM_MS, int(delay)))
        previous = character
    return values


def plan(text, rng=random):
    """Keystrokes to perform, as (key, pause_after_ms) pairs.

    Some of them are wrong. A person typing a long caption strikes a
    neighbouring key now and then, sees it, and takes it back, and the trace
    of that — a letter, a pause, a Backspace — is the hardest thing for a
    machine to fake by accident. The slips never land inside a hashtag or a
    mention, because a half-typed tag opens a suggestion list and the
    correction would then be fighting the platform's own UI.
    """
    steps, hot = [], False
    for character, delay in zip(text, intervals(text, rng)):
        if character in "#@":
            hot = True
        elif character.isspace():
            hot = False
        slip = (not hot and character.isalpha() and character.lower() in _NEIGHBOURS
                and rng.random() < TYPO_CHANCE)
        if slip:
            wrong = rng.choice(_NEIGHBOURS[character.lower()])
            steps.append((wrong.upper() if character.isupper() else wrong,
                          rng.randint(*TYPO_NOTICE_MS)))
            steps.append(("\b", rng.randint(*TYPO_RECOVER_MS)))
        steps.append((character, delay))
    return steps


async def type_text(page, text, rng=random):
    """Type one character at a time, at a pace that varies the way a hand does.

    Including the occasional wrong key, taken back immediately; see `plan`.
    """
    for key, delay in plan(text, rng):
        if key == "\b":
            await page.keyboard.press("Backspace")
        else:
            await page.keyboard.type(key)
        await page.wait_for_timeout(delay)


async def settle(page, rng=random):
    """Pause the way a person does between finishing one control and the next."""
    await page.wait_for_timeout(rng.randint(*SETTLE_MS))


def would_type(text):
    """Would a person type this from memory, or paste it from somewhere?

    Short, single-line, plain ASCII is typed. Anything longer, anything with a
    line break, and anything outside ASCII is pasted, because typing it leaves
    a trace no hand leaves: half an hour of flawless keystrokes, or characters
    that arrive with no keystroke at all.
    """
    return len(text) <= TYPED_LIMIT and "\n" not in text and text.isascii()


def clipboard_tools():
    """The write and read commands for this platform, or None if there are none."""
    return _CLIPBOARD.get("linux" if sys.platform.startswith("linux") else sys.platform)


def read_clipboard():
    tools = clipboard_tools()
    if not tools:
        return None
    try:
        done = subprocess.run(tools[1], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def write_clipboard(text):
    tools = clipboard_tools()
    if not tools:
        return False
    try:
        return subprocess.run(tools[0], input=text, text=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


async def paste(page, text, rng=random):
    """Paste through the system clipboard, so the browser raises the event.

    The clipboard is global: between writing our text and pressing the paste
    key, the person at the keyboard or another agent can copy something of
    their own, and what lands in the field would be theirs. A caption field
    that receives a password does not keep it private, because a draft editor
    saves what it holds.

    So the clipboard is re-read immediately before the key and the paste is
    abandoned if it is no longer ours, which leaves a window of microseconds
    rather than the half-second the write-then-press sequence used to span,
    and the caller reads the control back afterwards either way. The previous
    text is restored only when the clipboard still holds ours, so a copy made
    while we worked is never overwritten. Clipboard contents that are not text
    still cannot be preserved.

    Returns True when the paste key was pressed.
    """
    previous = read_clipboard()
    if not write_clipboard(text):
        return False
    pressed = False
    try:
        await page.wait_for_timeout(rng.randint(120, 320))
        if read_clipboard() != text:
            # Someone else wrote to the clipboard while we waited. Pressing the
            # paste key now would put their content into this field.
            return False
        await page.keyboard.press(PASTE_KEY)
        pressed = True
        await page.wait_for_timeout(PASTE_READ_MS)
    finally:
        # Put the old text back, but never over someone else's newer copy: if
        # the clipboard no longer holds ours, a person or another process
        # wrote to it while we worked, and theirs is the one that matters.
        if previous is not None and read_clipboard() == text:
            write_clipboard(previous)
    return pressed


def segments(text):
    """Split text into runs a hand can produce and runs it cannot.

    A keyboard cannot type an emoji: typing one produced an input event with
    no keystroke behind it. A person inserts it from the emoji picker, and the
    picker produces exactly that — text arriving without a keystroke. So the
    ASCII runs are typed and the rest is inserted, which is what a person at
    this keyboard would leave behind.
    """
    runs, current, kind = [], "", None
    for character in text:
        typable = character.isascii()
        if kind is None or typable == kind:
            current += character
        else:
            runs.append((kind, current))
            current = character
        kind = typable
    if current:
        runs.append((kind, current))
    return runs


async def type_segments(page, text, rng=random):
    """Type what a keyboard types, insert what a picker inserts."""
    for typable, run in segments(text):
        if typable:
            await type_text(page, run, rng)
        else:
            await page.keyboard.insert_text(run)
            await page.wait_for_timeout(rng.randint(180, 520))


async def enter_text(page, locator, text, rng=random, read_back=None, allow_clipboard=True):
    """Put text into a control the way a person would, and say which way that was.

    Nobody composes a caption in the upload form. They write it beforehand and
    paste it, and the upload form sees a paste. So text that a person would
    not retype from memory — anything long, multi-line, or outside ASCII — is
    pasted through the system clipboard, which is the only way to produce the
    paste event a browser raises for a person. Short single-line ASCII, a
    title or a link, is typed.

    `allow_clipboard=False` forces typing for a caller that must not touch the
    clipboard at all. The clipboard is global, so `paste` re-reads it
    immediately before the key and abandons the paste if it is no longer ours,
    and restores the previous text only if nobody else has written since.

    `read_back` is an async callable returning what the control now holds.
    It is what makes the clipboard path recoverable: a browser without access
    to the system clipboard, a headless one among them, swallows the paste key
    silently and leaves the field empty. When the text did not land, the
    control is cleared and typed into instead. The one thing never done here,
    on any path, is fabricating an event.
    """
    await click(page, locator, rng)
    await settle(page, rng)
    if allow_clipboard and not would_type(text) and await paste(page, text, rng):
        if read_back is None or await read_back() == text:
            return "pasted"
        await locator.press("ControlOrMeta+A")
        await locator.press("Backspace")
        await page.wait_for_timeout(120)
        await type_segments(page, text, rng)
        return "typed-after-paste-was-not-received"
    await type_segments(page, text, rng)
    return "typed"
