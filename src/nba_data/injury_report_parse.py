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
* A two-line reason arrives as three text lines: the GemBox generator
  centres it around the player row (continuation 7 pt above, anchor,
  continuation 7 pt below, rows 22-23 pt apart); the earlier generators
  print the row's own line first and the wrap 4 pt below (rows 18 pt
  apart).  Either way the anchor nearest in ``top`` is the right one.
* A pre-GemBox team cell may wrap (``Minnesota`` / ``Timberwolves``): the
  second line carries only ``Team`` and sits 8 pt below the first, which
  also carries the group's first player.  A ``Team`` alone on a line a
  whole row below the previous team is instead a new group's team printed
  above its first player, so the join is bounded by ``WRAP_TOLERANCE``.
* Placeholder phrases land in ``Category`` in the 2018-19 layout and in
  ``Reason`` from 2020-21 on; the ``Non-NBA Team`` row carries a
  ``Player Name`` of ``,`` and ``-`` in its other cells.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date, datetime

import pdfplumber

from nba_data.injury_reports import TITLE_PATTERN, parse_title


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
# A line carrying only ``Team`` at most this far below a line that carried a
# ``Team`` cell is the second line of a wrapped team cell (observed: 8 pt;
# rows are 18 pt or more apart).
WRAP_TOLERANCE = 12.0
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


@dataclass(frozen=True)
class Listing:
    page: int
    row_number: int
    game_date: date
    game_time_raw: str
    matchup_raw: str
    away_tricode: str
    home_tricode: str
    team_raw: str
    is_placeholder: bool
    placeholder_raw: str | None
    player_name_raw: str | None
    current_status_raw: str | None
    reason_raw: str | None
    category_raw: str | None
    previous_status_raw: str | None


@dataclass(frozen=True)
class ParsedReport:
    pages: int
    title_text: str | None
    title_printed_et: datetime | None
    listings: tuple[Listing, ...]


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


# --------------------------------------------------------------------------
# row assembly
# --------------------------------------------------------------------------

_CARRIED = ("Game Date", "Game Time", "Matchup", "Team")
_CONTINUATION = ("Reason", "Category")
_ROW_CELLS = ("Player Name", "Current Status", "Reason", "Category", "Previous Status")
_PHRASES = ("NOT YET SUBMITTED", "ALL PLAYERS AVAILABLE")
_NON_NBA = "Non-NBA Team"
_PHRASE_CELLS = ("Player Name", "Category", "Reason", "Team")


class _Anchor:
    """One listing under construction: its line, carried cells and continuations."""

    __slots__ = ("page", "top", "cells", "carried", "placeholder", "parts")

    def __init__(self, page: int, top: float, cells: dict[str, str], carried: dict[str, str | None],
                 placeholder: str | None) -> None:
        self.page = page
        self.top = top
        self.cells = cells
        self.carried = carried
        self.placeholder = placeholder
        # continuation text by column: (top, text) in reading order
        self.parts: dict[str, list[tuple[float, str]]] = {}


def _join_wrapped_teams(lines: list[tuple[float, dict[str, str]]]) -> list[tuple[float, dict[str, str]]]:
    """Fold a Team-only line directly below a Team-carrying line into that cell."""
    joined: list[tuple[float, dict[str, str]]] = []
    for top, cells in lines:
        if (
            set(cells) == {"Team"}
            and joined
            and "Team" in joined[-1][1]
            and 0.0 <= top - joined[-1][0] <= WRAP_TOLERANCE
        ):
            previous_top, previous = joined[-1]
            joined[-1] = (previous_top, {**previous, "Team": previous["Team"] + " " + cells["Team"]})
            continue
        joined.append((top, dict(cells)))
    return joined


def _placeholder_kind(cells: dict[str, str], team: str | None) -> str | None:
    """The placeholder phrase a line prints, or None for a player line."""
    if cells.get("Player Name") == "," and team == _NON_NBA:
        return _NON_NBA
    for label in _PHRASE_CELLS:
        if cells.get(label) in _PHRASES:
            return cells[label]
    return None


def _classify(cells: dict[str, str], team: str | None, where: str, label: str) -> tuple[str, str | None]:
    """``("anchor", phrase | None)``, ``("carry", None)`` or ``("continuation", None)``."""
    placeholder = _placeholder_kind(cells, team)
    if placeholder is not None:
        stray = {
            column: value for column, value in cells.items()
            if column in _ROW_CELLS and value not in (placeholder, ",", "-")
        }
        if stray:
            raise ReportParseError(f"{label}: unknown anchor without a player name {where}: {stray}")
        return "anchor", placeholder
    if cells.get("Player Name"):
        if cells["Player Name"] == ",":
            raise ReportParseError(f"{label}: unknown anchor without a player name {where}: {cells}")
        return "anchor", None
    if "Current Status" in cells or "Previous Status" in cells:
        raise ReportParseError(f"{label}: unknown anchor without a player name {where}: {cells}")
    if any(column in cells for column in _CONTINUATION):
        return "continuation", None
    return "carry", None


def _nearest_anchor(anchors: list[_Anchor], top: float) -> _Anchor:
    """The anchor closest in ``top``; a tie goes to the one above."""
    return min(anchors, key=lambda anchor: (abs(anchor.top - top), anchor.top))


def _text(anchor: _Anchor, column: str) -> str | None:
    pieces = list(anchor.parts.get(column, ()))
    if column in anchor.cells:
        pieces.append((anchor.top, anchor.cells[column]))
    if not pieces:
        return None
    return " ".join(text for _, text in sorted(pieces, key=lambda piece: piece[0]))


def _listing(anchor: _Anchor, row_number: int, label: str) -> Listing:
    where = f"(page {anchor.page}, row {row_number})"
    for column in _CARRIED:
        if not anchor.carried.get(column):
            raise ReportParseError(f"{label}: carried cell missing: {column} {where}")
    game_date_raw = anchor.carried["Game Date"]
    game_time_raw = anchor.carried["Game Time"]
    matchup_raw = anchor.carried["Matchup"]
    if not GAME_DATE_PATTERN.match(game_date_raw):
        raise ReportParseError(f"{label}: game date malformed: {game_date_raw!r} {where}")
    month, day, year = (int(piece) for piece in game_date_raw.split("/"))
    try:
        game_date = date(year, month, day)
    except ValueError:
        raise ReportParseError(f"{label}: game date malformed: {game_date_raw!r} {where}") from None
    if not GAME_TIME_PATTERN.match(game_time_raw):
        raise ReportParseError(f"{label}: game time malformed: {game_time_raw!r} {where}")
    if not MATCHUP_PATTERN.match(matchup_raw):
        raise ReportParseError(f"{label}: matchup malformed: {matchup_raw!r} {where}")
    away, home = matchup_raw.split("@")

    if anchor.placeholder is not None:
        if anchor.parts:
            raise ReportParseError(f"{label}: continuation line attached to a placeholder {where}")
        return Listing(
            page=anchor.page, row_number=row_number, game_date=game_date, game_time_raw=game_time_raw,
            matchup_raw=matchup_raw, away_tricode=away, home_tricode=home, team_raw=anchor.carried["Team"],
            is_placeholder=True, placeholder_raw=anchor.placeholder, player_name_raw=None,
            current_status_raw=None, reason_raw=None, category_raw=None, previous_status_raw=None,
        )
    status = anchor.cells.get("Current Status")
    if status not in STATUSES:
        raise ReportParseError(f"{label}: status not recognised: {status!r} {where}")
    return Listing(
        page=anchor.page, row_number=row_number, game_date=game_date, game_time_raw=game_time_raw,
        matchup_raw=matchup_raw, away_tricode=away, home_tricode=home, team_raw=anchor.carried["Team"],
        is_placeholder=False, placeholder_raw=None, player_name_raw=anchor.cells["Player Name"],
        current_status_raw=status, reason_raw=_text(anchor, "Reason"), category_raw=_text(anchor, "Category"),
        previous_status_raw=anchor.cells.get("Previous Status"),
    )


def assemble_listings(
    pages: list[tuple[Header, list[tuple[float, dict[str, str]]]]], *, report_id: str | None = None,
) -> tuple[Listing, ...]:
    """Turn the pages' text lines into listings, numbered in document order.

    Anchors are lines with a non-empty ``Player Name`` cell or a placeholder
    phrase (``NOT YET SUBMITTED`` / ``ALL PLAYERS AVAILABLE`` in Player Name,
    Category, Reason or Team; ``Non-NBA Team`` with a Player Name of ``,``).
    ``Game Date``, ``Game Time``, ``Matchup`` and ``Team`` carry forward from
    the line that prints them to the first anchor at or below it and on
    until replaced, across pages.  A line carrying only ``Team`` within
    ``WRAP_TOLERANCE`` below a Team-carrying line continues that cell.
    Non-anchor ``Reason``/``Category`` lines attach to the anchor nearest in
    ``top`` on their page (ties to the anchor above) and join with single
    spaces in reading order.  Any other line without a player name fails.

    Gates applied per listing, in order: carried cells present; game date,
    game time and matchup patterns; status in ``STATUSES`` for player rows.
    Every failure raises ``ReportParseError("<report_id or report>: ...")``.
    """
    label = report_id or "report"
    known = {column for header, _ in pages for column, _ in header.columns}
    carried: dict[str, str | None] = {column: None for column in _CARRIED}
    anchors: list[_Anchor] = []
    for page_number, (header, lines) in enumerate(pages, 1):
        page_anchors: list[_Anchor] = []
        continuations: list[tuple[float, dict[str, str]]] = []
        for top, cells in _join_wrapped_teams(lines):
            where = f"(page {page_number}, top {top:g})"
            unknown = set(cells) - known
            if unknown:
                raise ReportParseError(f"{label}: cell outside the header columns {where}: {sorted(unknown)}")
            for column in _CARRIED:
                if column in cells and cells[column] not in _PHRASES:
                    carried[column] = cells[column]
            kind, placeholder = _classify(cells, carried["Team"], where, label)
            if kind == "anchor":
                page_anchors.append(_Anchor(page_number, top, cells, dict(carried), placeholder))
            elif kind == "continuation":
                continuations.append((top, cells))
        for top, cells in continuations:
            if not page_anchors:
                raise ReportParseError(
                    f"{label}: continuation line without an anchor (page {page_number}, top {top:g})"
                )
            anchor = _nearest_anchor(page_anchors, top)
            for column in _CONTINUATION:
                if column in cells:
                    anchor.parts.setdefault(column, []).append((top, cells[column]))
        anchors.extend(page_anchors)
    return tuple(_listing(anchor, number, label) for number, anchor in enumerate(anchors, 1))


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------


def _title(words: list[dict]) -> str | None:
    match = TITLE_PATTERN.search(" ".join(str(word["text"]) for word in words))
    return match.group(0) if match is not None else None


def parse_report(payload: bytes, *, report_id: str | None = None) -> ParsedReport:
    """Parse one report body into its listings, failing closed on any gate.

    Gates, in order: the body opens with at least one page; page one prints
    a header and every later page either repeats a header with the same
    columns or inherits page one's; no word is left unassigned on any page;
    carried cells are present on every anchor; game date, game time and
    matchup match their patterns; every player row's status is one of
    ``STATUSES``; each page's footer reads ``Page i of N`` for its position
    and the page count; the page-one title parses.  Every failure raises
    ``ReportParseError`` prefixed with ``report_id`` (or ``report``).
    """
    label = report_id or "report"
    try:
        pdf = pdfplumber.open(io.BytesIO(payload))
    except Exception as error:   # pdfminer raises a zoo of exception types
        raise ReportParseError(f"{label}: body does not open ({type(error).__name__})") from None
    with pdf:
        try:
            pages = list(pdf.pages)
            if not pages:
                raise ReportParseError(f"{label}: body does not open (no pages)")
            title_text = _title(page_words(pages[0]))
            parsed: list[tuple[Header, list[tuple[float, dict[str, str]]], tuple[int, int]]] = []
            first: Header | None = None
            for page_number, page in enumerate(pages, 1):
                try:
                    header, lines, numbers = parse_page(page, page_number, inherited=first)
                except ReportParseError as error:
                    raise ReportParseError(f"{label}: {error}") from None
                if first is None:
                    first = header
                elif header.columns != first.columns:
                    raise ReportParseError(
                        f"{label}: header columns on page {page_number} differ from page 1"
                    )
                parsed.append((header, lines, numbers))
        except ReportParseError:
            raise
        except Exception as error:
            raise ReportParseError(f"{label}: body does not open ({type(error).__name__})") from None

    listings = assemble_listings([(header, lines) for header, lines, _ in parsed], report_id=report_id)
    for page_number, (_, _, numbers) in enumerate(parsed, 1):
        if numbers != (page_number, len(parsed)):
            raise ReportParseError(
                f"{label}: footer reads Page {numbers[0]} of {numbers[1]} on page {page_number} of {len(parsed)}"
            )
    printed = parse_title(title_text or "")
    if printed is None:
        raise ReportParseError(f"{label}: title does not parse: {title_text!r}")
    return ParsedReport(
        pages=len(parsed), title_text=title_text, title_printed_et=printed, listings=listings,
    )
