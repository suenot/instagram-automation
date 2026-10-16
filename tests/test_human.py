import ast
import pathlib
import random
import statistics

from instagram_automation import human

PACKAGE = pathlib.Path(human.__file__).resolve().parent


SENTENCE = ("Which word did not come from the Caribbean? Six words, one odd one "
            "out. Watch for the reveal. #WordOrigins")


def test_keystroke_pace_is_shaped_like_a_hand_not_a_metronome():
    rng = random.Random(3)
    values = human.intervals(SENTENCE, rng)
    assert len(values) == len(SENTENCE)
    assert min(values) >= human.KEY_MINIMUM_MS
    # locator.type(delay=55) measured 0-3 ms of spread; fill none at all.
    assert max(values) - min(values) > 200
    # Real intervals pile up near the median and trail off to the right, so
    # the mean sits well above the median. A symmetric draw does not do that.
    assert statistics.mean(values) > statistics.median(values) * 1.15


def test_the_sentence_interrupts_the_typing():
    rng = random.Random(3)
    values = human.intervals(SENTENCE, rng)
    assert sum(1 for value in values if value > 400) >= 3, \
        "A person stops now and then; this trace never does"


def test_a_line_break_costs_more_than_a_letter():
    rng = random.Random(5)
    letters = human.intervals("aaaaaaaaaa", random.Random(5))
    breaks = [value for character, value
              in zip("a\na\na\na\na\n", human.intervals("a\na\na\na\na\n", rng))
              if character == "\n"]
    assert min(breaks) > max(letters)


def test_the_plan_types_the_exact_text_even_when_it_slips():
    for seed in range(40):
        typed = ""
        for key, _ in human.plan(SENTENCE, random.Random(seed)):
            typed = typed[:-1] if key == "\b" else typed + key
        assert typed == SENTENCE, f"seed {seed} did not reconstruct the text"


def test_slips_happen_and_are_taken_back_immediately():
    corrections = 0
    for seed in range(40):
        steps = human.plan(SENTENCE, random.Random(seed))
        for index, (key, _) in enumerate(steps):
            if key == "\b":
                corrections += 1
                assert index > 0 and steps[index - 1][0] != "\b"
    assert corrections > 0, "No slip in 40 passes; a long caption is never flawless"


def test_a_hashtag_is_never_mistyped():
    """A half-typed tag opens the platform's suggestion list.

    A correction inside one would then be fighting that UI, so slips stop at
    the # or @ and resume at the next space.
    """
    text = "#WordOrigins #Caribbean @someone done"
    for seed in range(60):
        steps = human.plan(text, random.Random(seed))
        typed = ""
        for key, _ in steps:
            if key == "\b":
                assert "#" not in typed.split(" ")[-1] and "@" not in typed.split(" ")[-1]
                typed = typed[:-1]
            else:
                typed += key


def test_text_splits_into_what_a_keyboard_can_produce_and_what_it_cannot():
    runs = human.segments("Caribbean \U0001F334 today")
    assert "".join(run for _, run in runs) == "Caribbean \U0001F334 today"
    assert [typable for typable, _ in runs] == [True, False, True]
    assert human.segments("plain ascii only") == [(True, "plain ascii only")]


def test_a_person_types_what_they_know_and_pastes_what_they_composed():
    assert human.would_type("Short enough to retype.")
    assert not human.would_type("x" * (human.TYPED_LIMIT + 1))
    assert not human.would_type("first line\nsecond line")
    assert not human.would_type("Caribbean \U0001F334")


def test_clipboard_round_trip_and_restore():
    if not human.clipboard_tools():
        return  # no clipboard on this platform; enter_text falls back to typing
    before = human.read_clipboard()
    assert human.write_clipboard("caribbean awesome probe")
    assert human.read_clipboard() == "caribbean awesome probe"
    if before is not None:
        human.write_clipboard(before)
        assert human.read_clipboard() == before


def test_no_fabricated_or_keystrokeless_input_in_the_publisher():
    """Text must arrive the way a person's text arrives.

    A dispatched event carries isTrusted: false. execCommand, fill and
    insert_text carry nothing at all: measured on a probe page, each put 49
    characters in with a single input event and not one keystroke.
    """
    offenders = []
    for path in sorted(PACKAGE.glob("*.py")):
        if path.name == "human.py":
            continue  # the module that owns the one allowed insert_text
        source = path.read_text(encoding="utf-8")
        code = source
        # Prose about the old approach is not the old approach.
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    code = code.replace(doc, "")
        code = "\n".join(line for line in code.splitlines()
                          if not line.strip().startswith("#"))
        for marker in ("dispatchEvent", "new MouseEvent", "new KeyboardEvent",
                       "new Event(", "execCommand", ".fill(", "insert_text",
                       "press_sequentially"):
            if marker in code:
                offenders.append(f"{path.name}: {marker}")
    assert not offenders, f"Input that no hand produces: {offenders}"
