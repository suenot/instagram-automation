import ast
import pathlib
import random

from instagram_automation import human

PACKAGE = pathlib.Path(human.__file__).resolve().parent


def test_keystroke_pace_varies_the_way_a_hand_does():
    rng = random.Random(7)
    values = human.intervals("Word origins, island stories and Caribbean culture.", rng)
    assert len(values) == 51
    assert min(values) >= human.KEY_MINIMUM_MS
    # A metronome is the tell: this publisher used to type with delay=5, so
    # every keystroke sat the same 5 ms from the last.
    spread = max(values) - min(values)
    assert spread > 60, f"Keystroke pace is too regular to pass for a person: {spread} ms"
    assert len(set(values)) > 20


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
