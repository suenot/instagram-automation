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

# Timing drawn from the measurements above; the spread matters more than the
# centre, because a constant interval is the thing that reads as automation.
KEY_INTERVAL_MS = (105, 38)      # mean, standard deviation
KEY_MINIMUM_MS = 28
WORD_BREAK_MS = (10, 60)         # extra pause after a space
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


def intervals(text, rng=random):
    """The pause before each character, in milliseconds."""
    values = []
    for character in text:
        delay = rng.gauss(*KEY_INTERVAL_MS)
        if character == " ":
            delay += rng.randint(*WORD_BREAK_MS)
        values.append(max(KEY_MINIMUM_MS, int(delay)))
    return values


async def type_text(page, text, rng=random):
    """Type one character at a time, at a pace that varies the way a hand does."""
    for character, delay in zip(text, intervals(text, rng)):
        await page.keyboard.type(character)
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

    Off by default, and it must stay that way unless the machine is ours
    alone. The clipboard is global: between writing our text and pressing the
    paste key, the person at the keyboard or another agent can copy something
    of their own, and what lands in the field is theirs, not ours. A caption
    field that receives a password does not keep it private — a draft editor
    saves what it holds. Reading the control back catches the wrong text, but
    only after the editor has already seen it.

    The window is narrowed by confirming the clipboard still holds our text
    immediately before the key, and the previous text is put back afterwards.
    Narrowed is not closed, and clipboard contents that are not text cannot be
    put back at all.

    Returns True when the paste key was pressed.
    """
    previous = read_clipboard()
    if not write_clipboard(text):
        return False
    try:
        await page.wait_for_timeout(rng.randint(120, 320))
        if read_clipboard() != text:
            # Someone else wrote to the clipboard while we waited. Pressing the
            # paste key now would put their content into this field.
            return False
        await page.keyboard.press(PASTE_KEY)
        await page.wait_for_timeout(PASTE_READ_MS)
    finally:
        if previous is not None:
            write_clipboard(previous)
    return True


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


async def enter_text(page, locator, text, rng=random, read_back=None, allow_clipboard=False):
    """Put text into a control the way a person would, and say which way that was.

    The default never touches the system clipboard, because the clipboard is
    shared with the person at the machine and with every other agent on it.
    Text is typed, and the parts a keyboard cannot produce are inserted the
    way an emoji picker inserts them.

    `allow_clipboard` opts into a real paste event, which is a closer
    imitation of a person for a long caption but races everyone else for a
    global resource; see `paste`. Only pass it when nothing else is using this
    machine.

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
