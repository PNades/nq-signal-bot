"""Every info icon is an `InfoTip` (owner 2026-11-24, UI polish round 2): hover opens its box, a click pins it, a click
outside or Esc closes it, and the icon is a real button (a mouse click draws no focus ring; the keyboard keeps it).
Checked in the sources: no bare lucide `<Info>` icon is made focusable or given a hint (that was the old icon, which
kept Chrome's focus ring after a click), and the component keeps its parts."""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "ui" / "web" / "src"
INFO_TAG = re.compile(r"<Info\b[^>]*>", re.S)


def bare_info_icons(text: str) -> list[str]:
    return [m.group(0) for m in INFO_TAG.finditer(text) if re.search(r"\btabIndex\b|\bdata-hint\b|\bonClick\b", m.group(0))]


def test_no_info_icon_is_a_bare_focusable_svg():
    bad = {f.relative_to(SRC).as_posix(): bare_info_icons(f.read_text(encoding="utf-8")) for f in SRC.rglob("*.tsx")}
    assert not {k: v for k, v in bad.items() if v}, "use <InfoTip text=…> for an info icon"


def test_the_chart_header_uses_the_info_tip_and_the_tip_keeps_its_parts():
    header = (SRC / "pages" / "performance" / "lifecycle.tsx").read_text(encoding="utf-8")
    assert "<InfoTip text={hint} />" in header
    tip = (SRC / "components" / "ui" / "info-tip.tsx").read_text(encoding="utf-8")
    for part in ('<button ref={btn} type="button"', "onPointerEnter", "onPointerLeave", '"pinned"', '"Escape"',
                 '"pointerdown"', "focus:outline-none focus-visible:outline-2"):
        assert part in tip, part


def test_the_check_catches_the_old_icon():
    old = '<Info className="size-3 shrink-0 text-text-3" aria-label={hint} data-hint={hint} tabIndex={0} />'
    assert bare_info_icons(old) == [old]
    assert bare_info_icons('<Info className="size-4" aria-hidden="true" />') == []
