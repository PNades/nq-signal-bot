"""The panel's hover rule (owner 2026-11-16): in every list (dropdowns, menus, pickers, the command palette) nothing
stays highlighted after the pointer leaves the list; the arrow keys still highlight. Checked in the sources: a list
component that highlights a row when the pointer enters or moves over it must clear that highlight when the pointer
leaves (a `setX(-1)` / `setX(null)` on mouse or pointer leave). The screenshot checks of each build stage exercise it
in the browser too."""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "ui" / "web" / "src"
LIST = re.compile(r'role="(?:listbox|option|menu|menuitem)"')
# a row highlighted on hover: an onMouseEnter / onMouseMove / onMouseOver / onPointerEnter / onPointerMove handler (or a
# row's onHover prop) whose body calls a setter
ON_HOVER = re.compile(r"\bon(?:(?:Mouse|Pointer)(?:Enter|Move|Over)|Hover)=\{")
SETTER = re.compile(r"\bset(\w+)\(")


def _handler(text: str, start: int) -> str:
    """The handler's source from its opening brace to the matching closing one."""
    depth, i = 0, start
    while i < len(text):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[start:i + 1]
        i += 1
    return text[start:]


def hover_setters(text: str) -> set[str]:
    return {m.group(1) for h in ON_HOVER.finditer(text) for m in SETTER.finditer(_handler(text, h.end() - 1))}


def clears_on_leave(text: str, setter: str) -> bool:
    return re.search(r"on(?:Mouse|Pointer)Leave=\{\(\) => set" + setter + r"\((?:-1|null)\)", text) is not None


def test_every_list_clears_its_hover_highlight_when_the_pointer_leaves():
    checked = []
    for f in sorted(SRC.rglob("*.tsx")):
        text = f.read_text(encoding="utf-8")
        if not LIST.search(text):
            continue
        for setter in hover_setters(text):
            checked.append(f"{f.name}:{setter}")
            assert clears_on_leave(text, setter), (
                f"{f.relative_to(SRC)}: set{setter} highlights a row on hover but nothing clears it when the pointer "
                f"leaves the list (owner 2026-11-16 hover rule)")
    # the lists known today: the account switcher, the command palette, the preset picker, the month picker
    names = {c.split(":")[0] for c in checked}
    assert {"AccountSwitcher.tsx", "command-palette.tsx", "preset-picker.tsx", "MonthPicker.tsx"} <= names, checked


def test_the_rule_catches_a_list_that_forgets():
    bad = '<div role="listbox">{rows.map((r, i) => <div role="option" onMouseMove={() => setActive(i)} />)}</div>'
    assert hover_setters(bad) == {"Active"} and not clears_on_leave(bad, "Active")
    good = bad.replace('<div role="listbox">', '<div role="listbox" onMouseLeave={() => setActive(-1)}>')
    assert clears_on_leave(good, "Active")
