"""UI polish round 3 (owner 2026-11-24), checked in the sources: the footer's "cream" keeps one weight at every width
(only its size follows the width), and the calendar's day panel closes on a click outside it and on Esc, not only on
its X."""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "ui" / "web" / "src"


def test_the_footer_word_has_one_weight_at_every_width():
    page = (SRC / "shell" / "page.tsx").read_text(encoding="utf-8")
    assert re.search(r"^const FOOTER_WEIGHT = 900;$", page, re.M)
    assert "fontWeight={FOOTER_WEIGHT}" in page
    assert "footerWeight" not in page, "the weight may not follow the footer's width (owner 2026-11-24)"


def test_the_day_panel_closes_outside_and_on_escape():
    cal = (SRC / "pages" / "performance" / "PnlCalendar.tsx").read_text(encoding="utf-8")
    for part in ('document.addEventListener("pointerdown", down, true)', 'e.key === "Escape"',
                 "panelRef.current?.contains(e.target as Node)", "<motion.aside ref={panelRef}",
                 'aria-label="Close" onClick={() => setOpen(null)}'):
        assert part in cal, part
