"""A gameplan file -> Claude API content blocks (SPEC §3.4).

- .md / .txt: the text as-is.
- .pdf: sent whole as a document block (the API reads its text).
- .docx (also Google Docs exported as .docx): converted to Markdown (headings, paragraphs, lists, tables as
  pipe tables).
Anything else raises CannotConvert; step 9 lists those files for the owner.

Gameplans carry no images (owner, 2026-10-05): image links and embedded pictures are ignored.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TEXT_SUFFIXES = (".md", ".txt")
SUPPORTED_SUFFIXES = (*TEXT_SUFFIXES, ".pdf", ".docx")
MAX_PDF_BYTES = 32 * 1024 * 1024  # the Claude API's request size limit


class CannotConvert(Exception):
    pass


@dataclass
class Converted:
    kind: str                     # "text", "pdf" or "docx"
    blocks: list[dict[str, Any]]  # content blocks for the user message
    markdown: str | None          # the text sent (saved as the canonical YYYY-MM-DD.md copy); None for a PDF


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")  # an older Word "Save as text" export


def _text_file(path: Path) -> Converted:
    text = _read_text(path)
    if not text.strip():
        raise CannotConvert(f"{path.name}: the file is empty")
    return Converted("text", [{"type": "text", "text": text}], text)


def _pdf(path: Path) -> Converted:
    data = path.read_bytes()
    if not data.startswith(b"%PDF"):
        raise CannotConvert(f"{path.name}: not a PDF file")
    if len(data) > MAX_PDF_BYTES:
        raise CannotConvert(f"{path.name}: over the API's 32 MB request limit")
    block = {"type": "document",
             "source": {"type": "base64", "media_type": "application/pdf",
                        "data": base64.standard_b64encode(data).decode()}}
    return Converted("pdf", [block], None)


def _cell_text(cell) -> str:
    return " ".join(p.text.strip() for p in cell.paragraphs if p.text.strip()).replace("|", "\\|")


def _table_markdown(table) -> list[str]:
    rows = [[_cell_text(c) for c in row.cells] for row in table.rows]
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return out


def _paragraph_markdown(p) -> str:
    text = p.text.strip()
    style = (p.style.name if p.style is not None else "") or ""
    if text:
        if style.startswith("Heading") and style[-1:].isdigit():
            text = "#" * int(style[-1]) + " " + text
        elif style == "Title":
            text = "# " + text
        elif style.startswith("List"):
            text = "- " + text
    return text


def _docx(path: Path) -> Converted:
    try:
        import docx
        from docx.table import Table

        document = docx.Document(str(path))
    except Exception as e:  # corrupt or not really a .docx
        raise CannotConvert(f"{path.name}: can't open as .docx ({type(e).__name__}: {e})") from None
    lines: list[str] = []
    for item in document.iter_inner_content():
        if isinstance(item, Table):
            lines += [""] + _table_markdown(item) + [""]
        else:
            lines.append(_paragraph_markdown(item))
    markdown = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"
    if not markdown.strip():
        raise CannotConvert(f"{path.name}: no text in the document")
    return Converted("docx", [{"type": "text", "text": markdown}], markdown)


def convert(path: Path) -> Converted:
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return _text_file(path)
    if suffix == ".pdf":
        return _pdf(path)
    if suffix == ".docx":
        return _docx(path)
    raise CannotConvert(f"{path.name}: {suffix or 'no extension'} is not supported (save it as .md, .docx or .pdf)")
