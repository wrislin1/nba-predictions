"""Header-driven parsing of official NBA injury-report PDFs (Part B).

One parser covers every layout the league has printed since 2018-12-17
because nothing about the columns is assumed: the header row read from the
page fixes each column's label and left edge, and every word below the
header and above the ``Page i of N`` footer is assigned to the column that
contains its left edge.  Words that share a text line and a column form a
cell.  Row assembly (anchors, carry-forward, placeholders) and the
per-report gates build on these page-level pieces.

Geometry facts observed on the committed fixtures, which the code below
depends on:

* ``extract_words`` must run with ``x_tolerance`` at most 1.5; pdfplumber's
  default of 3.0 fuses the two words of a team cell into
  ``MinnesotaTimberwolves`` on the GemBox layout (2023 onwards).
* Two-word labels (``Game Date``, ``Player Name``, ``Current Status``,
  ``Previous Status``) arrive as separate words and are joined here.
* Body cells start 1.0 pt *left* of their bold header label in the
  pre-GemBox layouts and 1.0 pt *right* of it in the GemBox layout, so a
  column's left boundary carries a small slack (``COLUMN_TOLERANCE``).
* The pre-GemBox generators repeat the header on every page.  The GemBox
  generator prints it on page one only: later pages open with the title
  line and go straight into listing rows, so ``parse_page`` lets a caller
  pass the page-one header to inherit the columns on a header-less page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from nba_data.injury_reports import TITLE_PATTERN


REQUIRED_COLUMNS = (
    "Game Date", "Game Time", "Matchup", "Team", "Player Name", "Current Status", "Reason",
)
OPTIONAL_COLUMNS = ("Category", "Previous Status")
STATUSES = ("Available", "Probable", "Questionable", "Doubtful", "Out")
PLACEHOLDER_PHRASES = ("NOT YET SUBMITTED", "ALL PLAYERS AVAILABLE", "Non-NBA Team")
X_TOLERANCE = 1.5
# Points a word's left edge may sit left of a header label and still belong
# to that column (observed: exactly 1.0 pt in the pre-GemBox layouts).
COLUMN_TOLERANCE = 1.5
# Words whose ``top`` differs by more than this from a line's first word
# start a new text line.
LINE_TOLERANCE = 1.0
GAME_DATE_PATTERN = re.compile(r"^\d{2}/\d{2}/\d{4}$")
GAME_TIME_PATTERN = re.compile(r"^\d{2}:\d{2} \(ET\)$")
MATCHUP_PATTERN = re.compile(r"^[A-Z]{2,3}@[A-Z]{2,3}$")
FOOTER_PATTERN = re.compile(r"^Page (\d+) of (\d+)$")

_KNOWN_LABELS = frozenset(REQUIRED_COLUMNS + OPTIONAL_COLUMNS)
_TWO_WORD_LABELS = frozenset(label for label in _KNOWN_LABELS if " " in label)
_ONE_WORD_LABELS = frozenset(label for label in _KNOWN_LABELS if " " not in label)


class ReportParseError(ValueError):
    """A report page or report failed one of the fail-closed parsing gates."""


@dataclass(frozen=True)
class Header:
    columns: tuple[tuple[str, float], ...]   # (label, left x) in page order
    bottom: float


# --------------------------------------------------------------------------
# words and lines
# --------------------------------------------------------------------------


def page_words(page) -> list[dict]:
    """pdfplumber words for ``page`` at the contracted horizontal tolerance."""
    return list(page.extract_words(x_tolerance=X_TOLERANCE))


def group_lines(words: list[dict]) -> list[list[dict]]:
    """Group words into text lines by ``top``.

    Words are visited in ``(top, x0)`` order; a word more than
    ``LINE_TOLERANCE`` points below the line's first word starts a new line.
    Each returned line is sorted by ``x0``.
    """
    lines: list[list[dict]] = []
    line_top: float | None = None
    for word in sorted(words, key=lambda w: (float(w["top"]), float(w["x0"]))):
        top = float(word["top"])
        if line_top is None or abs(top - line_top) > LINE_TOLERANCE:
            lines.append([])
            line_top = top
        lines[-1].append(word)
    return [sorted(line, key=lambda w: float(w["x0"])) for line in lines]


def _line_text(line: list[dict]) -> str:
    return " ".join(str(word["text"]) for word in line)


def _line_top(line: list[dict]) -> float:
    return min(float(word["top"]) for word in line)


def _line_bottom(line: list[dict]) -> float:
    return max(float(word["bottom"]) for word in line)


# --------------------------------------------------------------------------
# header
# --------------------------------------------------------------------------


def _header_labels(line: list[dict]) -> tuple[tuple[str, float], ...] | None:
    """Join a line's words into column labels; None when the line is not a header."""
    texts = [str(word["text"]) for word in line]
    columns: list[tuple[str, float]] = []
    index = 0
    while index < len(texts):
        pair = " ".join(texts[index:index + 2])
        if index + 1 < len(texts) and pair in _TWO_WORD_LABELS:
            columns.append((pair, float(line[index]["x0"])))
            index += 2
        elif texts[index] in _ONE_WORD_LABELS:
            columns.append((texts[index], float(line[index]["x0"])))
            index += 1
        else:
            return None
    labels = [label for label, _ in columns]
    if len(set(labels)) != len(labels):
        return None
    if not set(REQUIRED_COLUMNS) <= set(labels):
        return None
    return tuple(columns)


def detect_header(words: list[dict]) -> Header:
    """Find the header line and return its columns in x order.

    The header is the first text line made only of column labels that
    carries every required label.  Raises ``ReportParseError`` when no line
    qualifies, including when a required column is missing.
    """
    for line in group_lines(words):
        columns = _header_labels(line)
        if columns is not None:
            return Header(columns=columns, bottom=_line_bottom(line))
    raise ReportParseError("header not found")


def column_for(header: Header, x0: float) -> str:
    """The label of the column whose left edge is the greatest at or below ``x0``.

    The last column runs to the page edge.  A word more than
    ``COLUMN_TOLERANCE`` points left of the first column belongs to no
    column and fails the page.
    """
    chosen: str | None = None
    for label, left in header.columns:
        if left - COLUMN_TOLERANCE <= x0:
            chosen = label
        else:
            break
    if chosen is None:
        raise ReportParseError(f"word left of the first column: x0={x0}")
    return chosen


def line_cells(line: list[dict], header: Header) -> dict[str, str]:
    """Join each column's words on one text line with single spaces, in x order."""
    pieces: dict[str, list[str]] = {}
    for word in sorted(line, key=lambda w: float(w["x0"])):
        label = column_for(header, float(word["x0"]))
        pieces.setdefault(label, []).append(str(word["text"]))
    return {label: " ".join(texts) for label, texts in pieces.items()}


# --------------------------------------------------------------------------
# footer
# --------------------------------------------------------------------------


def _footer_line(lines: list[list[dict]]) -> tuple[list[dict], tuple[int, int]] | None:
    for line in lines:
        match = FOOTER_PATTERN.match(_line_text(line))
        if match is not None:
            return line, (int(match.group(1)), int(match.group(2)))
    return None


def footer_numbers(words: list[dict]) -> tuple[int, int]:
    """Read ``(i, N)`` from the ``Page i of N`` footer line."""
    found = _footer_line(group_lines(words))
    if found is None:
        raise ReportParseError("footer not found")
    return found[1]


# --------------------------------------------------------------------------
# page
# --------------------------------------------------------------------------


def parse_page(
    page, page_number: int, *, inherited: Header | None = None,
) -> tuple[Header, list[tuple[float, dict[str, str]]], tuple[int, int]]:
    """Read one page into its header, its body lines and its footer numbers.

    Returns ``(header, lines, (i, N))`` where ``lines`` holds
    ``(top, cells)`` for every text line below the header and above the
    footer, in reading order, with ``cells`` keyed by header label.  The
    header line, anything above it (the title) and the footer line are
    excluded.  Every other word must land in a column: a word left of the
    first column or below the footer fails the page.

    ``inherited`` is the page-one header of the same report.  When the page
    prints no header of its own (the GemBox generator does so only on page
    one) its columns are reused and the title line, when present, is the
    only thing kept above the body; without it the page fails.
    """
    words = page_words(page)
    lines = group_lines(words)
    try:
        header = detect_header(words)
    except ReportParseError as error:
        if inherited is None:
            raise ReportParseError(f"page {page_number}: {error}") from None
        title_bottom = 0.0
        for line in lines:
            if TITLE_PATTERN.search(_line_text(line)):
                title_bottom = max(title_bottom, _line_bottom(line))
        header = Header(columns=inherited.columns, bottom=title_bottom)

    found = _footer_line(lines)
    if found is None:
        raise ReportParseError(f"page {page_number}: footer not found")
    footer_line, numbers = found
    footer_top = _line_top(footer_line)

    body: list[tuple[float, dict[str, str]]] = []
    assigned = 0
    for line in lines:
        top = _line_top(line)
        if line is footer_line:
            assigned += len(line)
            continue
        if top < header.bottom:
            # the title and the header itself
            assigned += len(line)
            continue
        if top >= footer_top:
            raise ReportParseError(
                f"page {page_number}: word below the footer: {_line_text(line)!r}"
            )
        try:
            cells = line_cells(line, header)
        except ReportParseError as error:
            raise ReportParseError(f"page {page_number}: {error}") from None
        assigned += len(line)
        body.append((top, cells))
    if assigned != len(words):
        raise ReportParseError(f"page {page_number}: {len(words) - assigned} words unassigned")
    return header, body, numbers
