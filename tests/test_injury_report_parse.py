"""Tests for header-driven page geometry of official injury-report PDFs."""

from __future__ import annotations

import json
from pathlib import Path

import pdfplumber
import pytest

from nba_data import injury_report_parse as parse
from nba_data.injury_report_parse import Header, ReportParseError


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/injury_reports"
SOURCES = json.loads((FIXTURES / "SOURCES.json").read_text(encoding="utf-8"))

LAYOUT_2018 = (
    "Game Date", "Game Time", "Matchup", "Team", "Player Name",
    "Category", "Reason", "Current Status", "Previous Status",
)
LAYOUT_2019 = (
    "Game Date", "Game Time", "Matchup", "Team", "Player Name",
    "Current Status", "Reason", "Previous Status",
)
LAYOUT_2021 = (
    "Game Date", "Game Time", "Matchup", "Team", "Player Name",
    "Current Status", "Reason",
)

# (expected header labels, does every page print the header?).  The GemBox
# generator behind the 2023+ reports prints the header on page one only;
# later pages open with the title line and go straight into listing rows.
# The 2019-09-30 preseason report still prints the 2018-19 layout; the
# 2019-20 order (LAYOUT_2019) is covered synthetically below.
LAYOUTS = {
    "Injury-Report_2018-12-17_05PM.pdf": (LAYOUT_2018, True),
    "Injury-Report_2019-02-15_01PM.pdf": (LAYOUT_2018, True),
    "Injury-Report_2019-09-30_01PM.pdf": (LAYOUT_2018, True),
    "Injury-Report_2021-04-01_05PM.pdf": (LAYOUT_2021, True),
    "Injury-Report_2024-01-31_05PM.pdf": (LAYOUT_2021, False),
    "Injury-Report_2026-01-10_12_00AM.pdf": (LAYOUT_2021, False),
}


def word(text: str, x0: float, top: float, *, width: float | None = None, height: float = 8.0) -> dict:
    """A pdfplumber-shaped word dict for synthetic pages."""
    if width is None:
        width = 4.0 * len(text)
    return {"text": text, "x0": x0, "x1": x0 + width, "top": top, "bottom": top + height}


HEADER_2018 = [
    ("Game", 21.0), ("Date", 42.38), ("Game", 73.63), ("Time", 95.01),
    ("Matchup", 126.26), ("Team", 178.89), ("Player", 269.11), ("Name", 291.73),
    ("Category", 381.89), ("Reason", 494.67), ("Current", 607.44), ("Status", 634.53),
    ("Previous", 720.22), ("Status", 750.66),
]


def header_words(top: float = 58.0, *, drop: str | None = None) -> list[dict]:
    return [word(text, x0, top) for text, x0 in HEADER_2018 if text != drop]


class FakePage:
    """The one pdfplumber method ``page_words`` relies on."""

    def __init__(self, words: list[dict]) -> None:
        self._words = words
        self.calls: list[dict] = []

    def extract_words(self, **kwargs):
        self.calls.append(kwargs)
        return list(self._words)


# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------


def test_module_constants_match_the_contract() -> None:
    assert parse.REQUIRED_COLUMNS == (
        "Game Date", "Game Time", "Matchup", "Team", "Player Name", "Current Status", "Reason",
    )
    assert parse.OPTIONAL_COLUMNS == ("Category", "Previous Status")
    assert parse.STATUSES == ("Available", "Probable", "Questionable", "Doubtful", "Out")
    assert parse.PLACEHOLDER_PHRASES == ("NOT YET SUBMITTED", "ALL PLAYERS AVAILABLE", "Non-NBA Team")
    assert parse.X_TOLERANCE == 1.5
    assert parse.GAME_DATE_PATTERN.match("12/17/2018") and not parse.GAME_DATE_PATTERN.match("12/17/18")
    assert parse.GAME_TIME_PATTERN.match("07:00 (ET)") and not parse.GAME_TIME_PATTERN.match("7:00 (ET)")
    assert parse.MATCHUP_PATTERN.match("MIL@DET") and parse.MATCHUP_PATTERN.match("UNK@UNK")
    assert not parse.MATCHUP_PATTERN.match("MIL@DETX")
    assert parse.FOOTER_PATTERN.match("Page 3 of 7").groups() == ("3", "7")
    assert issubclass(ReportParseError, ValueError)


# --------------------------------------------------------------------------
# header detection
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("file_name", "layout"), sorted(LAYOUTS.items()))
def test_detect_header_reads_each_layout_in_page_order(file_name, layout) -> None:
    """Would fail if two-word labels split, columns came out of x order, or a
    layout's page-one header went unread."""
    expected_labels, header_on_every_page = layout
    with pdfplumber.open(FIXTURES / file_name) as pdf:
        assert len(pdf.pages) == SOURCES[file_name]["pages"]
        for page_number, page in enumerate(pdf.pages, 1):
            words = parse.page_words(page)
            if page_number > 1 and not header_on_every_page:
                with pytest.raises(ReportParseError, match="header not found"):
                    parse.detect_header(words)
                continue
            header = parse.detect_header(words)
            assert isinstance(header, Header)
            labels = tuple(label for label, _ in header.columns)
            assert labels == expected_labels
            lefts = [left for _, left in header.columns]
            assert all(a < b for a, b in zip(lefts, lefts[1:]))
            assert set(parse.REQUIRED_COLUMNS) <= set(labels)
            label_words = [w for w in words if w["text"] in {"Game", "Matchup", "Reason"}]
            header_line = [w for w in label_words if abs(w["top"] - label_words[0]["top"]) <= 1.0]
            assert header.bottom == pytest.approx(max(w["bottom"] for w in header_line))
            assert all(w["bottom"] <= header.bottom for w in header_line)


def test_detect_header_fails_when_a_required_column_is_missing() -> None:
    with pytest.raises(ReportParseError, match="header not found"):
        parse.detect_header(header_words(drop="Reason"))
    with pytest.raises(ReportParseError, match="header not found"):
        parse.detect_header(header_words(drop="Matchup"))
    # a split two-word label is not a header either
    with pytest.raises(ReportParseError, match="header not found"):
        parse.detect_header(header_words(drop="Name"))
    with pytest.raises(ReportParseError, match="header not found"):
        parse.detect_header([])

    header = parse.detect_header(header_words() + [word("12/17/2018", 20.0, 77.0)])
    assert tuple(label for label, _ in header.columns) == LAYOUT_2018
    assert header.columns[0] == ("Game Date", 21.0)
    assert header.columns[-1] == ("Previous Status", 720.22)
    assert header.bottom == 66.0

    # the optional columns may be absent; the header is still read
    reduced = [w for w in header_words() if w["text"] not in {"Category", "Previous"}]
    reduced = [w for w in reduced if not (w["text"] == "Status" and w["x0"] == 750.66)]
    labels = tuple(label for label, _ in parse.detect_header(reduced).columns)
    assert labels == LAYOUT_2021[:5] + ("Reason", "Current Status")

    # the 2019-20 order, which no committed fixture prints
    order_2019 = [
        ("Game", 21.0), ("Date", 42.0), ("Game", 73.0), ("Time", 95.0), ("Matchup", 126.0),
        ("Team", 178.0), ("Player", 269.0), ("Name", 291.0), ("Current", 381.0), ("Status", 408.0),
        ("Reason", 494.0), ("Previous", 720.0), ("Status", 750.0),
    ]
    header = parse.detect_header([word(text, x0, 58.0) for text, x0 in order_2019])
    assert tuple(label for label, _ in header.columns) == LAYOUT_2019

    # a header line is only labels: an extra word on it means it is not the header
    with pytest.raises(ReportParseError, match="header not found"):
        parse.detect_header(header_words() + [word("extra", 800.0, 58.0)])


# --------------------------------------------------------------------------
# column assignment
# --------------------------------------------------------------------------


def test_column_for_assigns_by_left_edge_and_last_column_runs_to_the_page_edge() -> None:
    header = Header(columns=(("Game Date", 21.0), ("Team", 178.89), ("Reason", 494.67)), bottom=66.0)

    assert parse.column_for(header, 21.0) == "Game Date"
    assert parse.column_for(header, 100.0) == "Game Date"
    assert parse.column_for(header, 178.89) == "Team"
    assert parse.column_for(header, 490.0) == "Team"
    assert parse.column_for(header, 494.67) == "Reason"
    assert parse.column_for(header, 800.0) == "Reason"
    assert parse.column_for(header, 10_000.0) == "Reason"

    # body cells sit 1 pt left of the bold header labels in the pre-GemBox
    # layouts (and 1 pt right in the GemBox layout); both land in the column
    assert parse.column_for(header, 20.0) == "Game Date"
    assert parse.column_for(header, 177.89) == "Team"
    assert parse.column_for(header, 493.67) == "Reason"
    assert parse.column_for(header, 21.0 - parse.COLUMN_TOLERANCE) == "Game Date"

    with pytest.raises(ReportParseError, match="word left of the first column"):
        parse.column_for(header, 21.0 - parse.COLUMN_TOLERANCE - 0.01)
    with pytest.raises(ReportParseError, match="word left of the first column"):
        parse.column_for(header, 0.0)


# --------------------------------------------------------------------------
# words
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("file_name", "page_number", "fuses_by_default"),
    [
        ("Injury-Report_2018-12-17_05PM.pdf", 1, False),
        ("Injury-Report_2024-01-31_05PM.pdf", 3, True),
        ("Injury-Report_2026-01-10_12_00AM.pdf", 1, True),
    ],
)
def test_page_words_keeps_adjacent_cells_separate(file_name, page_number, fuses_by_default) -> None:
    """Would fail if the word tolerance let pdfplumber fuse a team's two words."""
    with pdfplumber.open(FIXTURES / file_name) as pdf:
        page = pdf.pages[page_number - 1]
        texts = [w["text"] for w in parse.page_words(page)]
        default_texts = [w["text"] for w in page.extract_words()]

    assert "Minnesota" in texts
    assert "Timberwolves" in texts
    assert "MinnesotaTimberwolves" not in texts
    assert not any("Minnesota" in t and t != "Minnesota" for t in texts)
    assert ("MinnesotaTimberwolves" in default_texts) is fuses_by_default
    assert all(" " not in t for t in texts)


def test_page_words_passes_the_contracted_tolerance() -> None:
    page = FakePage([word("x", 30.0, 100.0)])
    assert parse.page_words(page) == [word("x", 30.0, 100.0)]
    assert page.calls == [{"x_tolerance": parse.X_TOLERANCE}]


# --------------------------------------------------------------------------
# lines and cells
# --------------------------------------------------------------------------


def test_group_lines_and_line_cells_join_words_within_a_column() -> None:
    header = parse.detect_header(header_words())
    row = [
        word("Sprain", 540.0, 77.0),
        word("12/17/2018", 20.0, 77.0),
        word("Ankle;", 520.0, 77.9),      # within 1.0 pt of the line's first word
        word("Left", 493.67, 77.0),
        word("Pistons", 210.0, 77.0),
        word("Detroit", 177.89, 77.0),
        word("(ET)", 95.0, 77.0),
        word("07:00", 72.63, 77.0),
        word("MIL@DET", 125.26, 77.0),
        word("Henry", 310.0, 77.0),
        word("Ellenson,", 268.11, 77.0),
        word("Injury/Illness", 380.89, 77.0),
        word("Out", 606.44, 77.0),
        word("-", 719.22, 77.0),
    ]
    second = [word("Pachulia,", 268.11, 95.0), word("Zaza", 310.0, 95.0)]
    just_too_far = [word("Questionable", 606.44, 78.1)]   # 1.1 pt below: its own line

    lines = parse.group_lines(header_words() + second + row + just_too_far)

    assert [len(line) for line in lines] == [len(HEADER_2018), len(row), 1, len(second)]
    assert [line[0]["top"] for line in lines] == [58.0, 77.0, 78.1, 95.0]
    assert [w["text"] for w in lines[1]] == [
        "12/17/2018", "07:00", "(ET)", "MIL@DET", "Detroit", "Pistons", "Ellenson,", "Henry",
        "Injury/Illness", "Left", "Ankle;", "Sprain", "Out", "-",
    ]
    assert parse.group_lines([]) == []

    assert parse.line_cells(lines[1], header) == {
        "Game Date": "12/17/2018",
        "Game Time": "07:00 (ET)",
        "Matchup": "MIL@DET",
        "Team": "Detroit Pistons",
        "Player Name": "Ellenson, Henry",
        "Category": "Injury/Illness",
        "Reason": "Left Ankle; Sprain",
        "Current Status": "Out",
        "Previous Status": "-",
    }
    assert parse.line_cells(lines[3], header) == {"Player Name": "Pachulia, Zaza"}
    # words are joined in x order whatever order they arrive in
    assert parse.line_cells(list(reversed(lines[1])), header)["Reason"] == "Left Ankle; Sprain"
    with pytest.raises(ReportParseError, match="word left of the first column"):
        parse.line_cells([word("stray", 5.0, 77.0)], header)


# --------------------------------------------------------------------------
# footer
# --------------------------------------------------------------------------


def test_footer_numbers_reads_page_i_of_n() -> None:
    with pdfplumber.open(FIXTURES / "Injury-Report_2024-01-31_05PM.pdf") as pdf:
        assert parse.footer_numbers(parse.page_words(pdf.pages[2])) == (3, 7)
        assert parse.footer_numbers(parse.page_words(pdf.pages[6])) == (7, 7)
    with pdfplumber.open(FIXTURES / "Injury-Report_2018-12-17_05PM.pdf") as pdf:
        assert parse.footer_numbers(parse.page_words(pdf.pages[3])) == (4, 4)

    footer = [word("Page", 400.0, 573.66), word("2", 420.9, 573.66), word("of", 427.58, 573.66), word("4", 436.47, 573.66)]
    assert parse.footer_numbers(header_words() + footer) == (2, 4)
    with pytest.raises(ReportParseError, match="footer not found"):
        parse.footer_numbers(header_words())
    with pytest.raises(ReportParseError, match="footer not found"):
        parse.footer_numbers(footer[:3])
    with pytest.raises(ReportParseError, match="footer not found"):
        parse.footer_numbers(footer + [word("extra", 450.0, 573.66)])


# --------------------------------------------------------------------------
# parse_page
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("file_name", "layout"), sorted(LAYOUTS.items()))
def test_parse_page_returns_header_lines_below_header_above_footer_and_footer(file_name, layout) -> None:
    """Would fail if a word went unassigned, a title or footer word leaked into
    a cell, or a GemBox page after the first lost its inherited columns."""
    expected_labels, header_on_every_page = layout
    page_count = SOURCES[file_name]["pages"]
    with pdfplumber.open(FIXTURES / file_name) as pdf:
        first_header = None
        for page_number, page in enumerate(pdf.pages, 1):
            words = parse.page_words(page)
            if page_number > 1 and not header_on_every_page:
                with pytest.raises(ReportParseError, match=f"page {page_number}: header not found"):
                    parse.parse_page(page, page_number)
            header, lines, footer = parse.parse_page(page, page_number, inherited=first_header)
            if page_number == 1:
                first_header = header

            assert footer == (page_number, page_count)
            assert tuple(label for label, _ in header.columns) == expected_labels
            assert header.columns == first_header.columns
            tops = [top for top, _ in lines]
            assert tops == sorted(tops) and len(set(tops)) == len(tops)
            assert all(top >= header.bottom for top in tops)

            footer_top = min(w["top"] for w in words if w["text"] == "Page")
            assert all(top < footer_top for top in tops)
            # every word between the header and the footer ends up in exactly one cell
            body_words = [w for w in words if header.bottom <= w["top"] < footer_top]
            excluded = [w for w in words if w not in body_words]
            assert len(excluded) == len(words) - len(body_words)
            above = [w for w in excluded if w["top"] < header.bottom]
            footer_words = [w for w in excluded if w["top"] >= footer_top]
            assert len(above) + len(footer_words) == len(excluded)
            assert " ".join(w["text"] for w in sorted(footer_words, key=lambda w: w["x0"])) == (
                f"Page {page_number} of {page_count}"
            )
            assert "Injury Report:" in " ".join(w["text"] for w in sorted(above, key=lambda w: (w["top"], w["x0"])))
            cell_words = [piece for _, cells in lines for value in cells.values() for piece in value.split(" ")]
            assert sorted(cell_words) == sorted(w["text"] for w in body_words)
            assert len(body_words) > 0

            for _, cells in lines:
                assert cells
                assert set(cells) <= set(expected_labels)
                for value in cells.values():
                    assert value == value.strip() and value and "  " not in value
                    assert "Injury Report:" not in value
                    assert not parse.FOOTER_PATTERN.match(value)
                assert "Page" not in cells.values()
            assert not any(
                tuple(cells.get(label) for label in expected_labels) == expected_labels for _, cells in lines
            )


def test_parse_page_reads_known_lines_from_each_generator() -> None:
    with pdfplumber.open(FIXTURES / "Injury-Report_2018-12-17_05PM.pdf") as pdf:
        header, lines, footer = parse.parse_page(pdf.pages[0], 1)
        assert footer == (1, 4)
        assert lines[0][1] == {
            "Game Date": "12/17/2018",
            "Game Time": "07:00 (ET)",
            "Matchup": "MIL@DET",
            "Team": "Detroit Pistons",
            "Player Name": "Ellenson, Henry",
            "Category": "Injury/Illness",
            "Reason": "Left Ankle Sprain",
            "Current Status": "Out",
            "Previous Status": "-",
        }
        # the wrapped team cell arrives as two lines, 8 pt apart
        minnesota = next(i for i, (_, c) in enumerate(lines) if c.get("Team") == "Minnesota")
        assert lines[minnesota][1]["Matchup"] == "SAC@MIN"
        assert lines[minnesota][1]["Player Name"] == "Bates-Diop, Keita"
        assert lines[minnesota + 1][1] == {"Team": "Timberwolves"}
        assert lines[minnesota + 1][0] - lines[minnesota][0] == pytest.approx(8.0)

        _, lines, footer = parse.parse_page(pdf.pages[2], 3)
        assert footer == (3, 4)
        # placeholders land in the Category column in the 2018-19 layout
        assert (149.0, {"Team": "Portland Trail Blazers", "Category": "ALL PLAYERS AVAILABLE"}) in lines
        assert (167.0, {
            "Game Date": "12/18/2018", "Game Time": "07:00 (ET)", "Matchup": "CLE@IND",
            "Team": "Cleveland Cavaliers", "Category": "NOT YET SUBMITTED",
        }) in lines

    with pdfplumber.open(FIXTURES / "Injury-Report_2019-02-15_01PM.pdf") as pdf:
        _, lines, footer = parse.parse_page(pdf.pages[0], 1)
        assert footer == (1, 1)
        assert lines[0][1] == {
            "Game Date": "02/15/2019", "Game Time": "07:00 (ET)", "Matchup": "UNK@UNK",
            "Team": "Non-NBA Team", "Player Name": ",", "Category": "-", "Reason": "-",
            "Previous Status": "-",
        }
        assert lines[1][1] == {"Player Name": ",", "Category": "-", "Reason": "-", "Previous Status": "-"}

    with pdfplumber.open(FIXTURES / "Injury-Report_2021-04-01_05PM.pdf") as pdf:
        _, lines, footer = parse.parse_page(pdf.pages[3], 4)
        assert footer == (4, 4)
        # placeholders land in the Reason column from the 2020-21 layout on
        assert lines[0][1] == {
            "Game Time": "09:00 (ET)", "Matchup": "ATL@NOP", "Team": "Atlanta Hawks",
            "Reason": "NOT YET SUBMITTED",
        }
        assert lines[1][1] == {"Team": "New Orleans Pelicans", "Reason": "NOT YET SUBMITTED"}

    with pdfplumber.open(FIXTURES / "Injury-Report_2024-01-31_05PM.pdf") as pdf:
        header, lines, footer = parse.parse_page(pdf.pages[0], 1)
        assert footer == (1, 7)
        assert header.bottom == pytest.approx(117.74, abs=0.01)
        assert lines[0][1] == {
            "Game Date": "01/31/2024",
            "Game Time": "07:00 (ET)",
            "Matchup": "CHI@CHA",
            "Team": "Chicago Bulls",
            "Player Name": "Ball, Lonzo",
            "Current Status": "Out",
            "Reason": "Injury/Illness - Left Knee; Surgery",
        }
        # the centred two-line reason around Craig, Torrey arrives as three lines
        craig = next(i for i, (_, c) in enumerate(lines) if c.get("Player Name") == "Craig, Torrey")
        assert lines[craig - 1][1] == {"Reason": "Injury/Illness - Right Plantar Fascia;"}
        assert lines[craig][1] == {"Player Name": "Craig, Torrey", "Current Status": "Out"}
        assert lines[craig + 1][1] == {"Reason": "Sprain"}
        assert lines[craig][0] - lines[craig - 1][0] == pytest.approx(7.02, abs=0.05)

        inherited, lines, footer = parse.parse_page(pdf.pages[1], 2, inherited=header)
        assert footer == (2, 7)
        assert inherited.columns == header.columns
        # the title line is the only thing above the body on a header-less page
        assert inherited.bottom == pytest.approx(67.53, abs=0.01)
        assert lines[0][1] == {
            "Player Name": "Stewart, Isaiah",
            "Current Status": "Out",
            "Reason": "Injury/Illness - Left Ankle; Sprain",
        }
        assert lines[0][0] == pytest.approx(82.61, abs=0.01)
        assert lines[2][1]["Team"] == "Cleveland Cavaliers"


def test_parse_page_fails_closed_on_unassigned_words() -> None:
    footer = [word("Page", 400.0, 573.66), word("1", 420.9, 573.66), word("of", 427.58, 573.66), word("1", 436.47, 573.66)]
    body = [word("12/17/2018", 20.0, 77.0), word("Ellenson,", 268.11, 77.0), word("Out", 606.44, 77.0)]
    title = [word("Injury", 300.0, 20.0), word("Report:", 330.0, 20.0), word("12/17/18", 370.0, 20.0),
             word("05:30", 410.0, 20.0), word("PM", 440.0, 20.0)]

    header, lines, numbers = parse.parse_page(FakePage(title + header_words() + body + footer), 1)
    assert numbers == (1, 1)
    assert lines == [(77.0, {"Game Date": "12/17/2018", "Player Name": "Ellenson,", "Current Status": "Out"})]

    with pytest.raises(ReportParseError, match="page 1: word left of the first column"):
        parse.parse_page(FakePage(header_words() + body + [word("stray", 2.0, 95.0)] + footer), 1)
    with pytest.raises(ReportParseError, match="page 1: word below the footer"):
        parse.parse_page(FakePage(header_words() + body + footer + [word("stray", 300.0, 590.0)]), 1)
    with pytest.raises(ReportParseError, match="page 2: footer not found"):
        parse.parse_page(FakePage(header_words() + body), 2)
    with pytest.raises(ReportParseError, match="page 2: header not found"):
        parse.parse_page(FakePage(title + body + footer), 2)

    # a header-less page inherits the columns and keeps only the title above the body
    inherited, lines, numbers = parse.parse_page(FakePage(title + body + footer), 2, inherited=header)
    assert inherited.columns == header.columns
    assert inherited.bottom == 28.0
    assert lines == [(77.0, {"Game Date": "12/17/2018", "Player Name": "Ellenson,", "Current Status": "Out"})]
    # without a title line nothing sits above the body
    inherited, lines, _ = parse.parse_page(FakePage(body + footer), 2, inherited=header)
    assert inherited.bottom == 0.0
    assert len(lines) == 1


# --------------------------------------------------------------------------
# row assembly and parse_report (Task 10)
# --------------------------------------------------------------------------

import csv
import io
from datetime import date, datetime

from nba_data.injury_report_parse import Listing, ParsedReport

REPORT_IDS = [name[:-4] for name in sorted(LAYOUTS)]
EXPECTED_COLUMNS = [
    "page", "row_number", "game_date", "game_time_raw", "matchup_raw", "away_tricode", "home_tricode",
    "team_raw", "is_placeholder", "placeholder_raw", "player_name_raw", "current_status_raw",
    "reason_raw", "category_raw", "previous_status_raw",
]
# the title printed on page one of each fixture, read from the rendered page
PRINTED_TITLES = {
    "Injury-Report_2018-12-17_05PM": ("Injury Report: 12/17/18 05:30 PM", datetime(2018, 12, 17, 17, 30)),
    "Injury-Report_2019-02-15_01PM": ("Injury Report: 02/15/19 01:30 PM", datetime(2019, 2, 15, 13, 30)),
    "Injury-Report_2019-09-30_01PM": ("Injury Report: 09/30/19 01:30 PM", datetime(2019, 9, 30, 13, 30)),
    "Injury-Report_2021-04-01_05PM": ("Injury Report: 04/01/21 05:30 PM", datetime(2021, 4, 1, 17, 30)),
    "Injury-Report_2024-01-31_05PM": ("Injury Report: 01/31/24 05:30 PM", datetime(2024, 1, 31, 17, 30)),
    "Injury-Report_2026-01-10_12_00AM": ("Injury Report: 01/10/26 12:00 AM", datetime(2026, 1, 10, 0, 0)),
}

H2018 = parse.detect_header(header_words())
H2021 = Header(
    columns=(
        ("Game Date", 21.0), ("Game Time", 94.0), ("Matchup", 168.0), ("Team", 232.0),
        ("Player Name", 360.0), ("Current Status", 486.0), ("Reason", 593.0),
    ),
    bottom=66.0,
)
GROUP = {"Game Date": "12/17/2018", "Game Time": "07:00 (ET)", "Matchup": "MIL@DET", "Team": "Detroit Pistons"}


def player(name: str, status: str = "Out", reason: str | None = "Illness", **cells) -> dict:
    row = {"Player Name": name, "Current Status": status}
    if reason is not None:
        row["Reason"] = reason
    row.update(cells)
    return row


def expected_listings(report_id: str) -> tuple[Listing, ...]:
    """The hand-checked rows, typed from the rendered pages (the oracle)."""
    with (FIXTURES / f"{report_id}.expected.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == EXPECTED_COLUMNS
        rows = list(reader)

    def optional(value: str) -> str | None:
        return value if value != "" else None

    listings = []
    for row in rows:
        listings.append(Listing(
            page=int(row["page"]),
            row_number=int(row["row_number"]),
            game_date=date.fromisoformat(row["game_date"]),
            game_time_raw=row["game_time_raw"],
            matchup_raw=row["matchup_raw"],
            away_tricode=row["away_tricode"],
            home_tricode=row["home_tricode"],
            team_raw=row["team_raw"],
            is_placeholder={"true": True, "false": False}[row["is_placeholder"]],
            placeholder_raw=optional(row["placeholder_raw"]),
            player_name_raw=optional(row["player_name_raw"]),
            current_status_raw=optional(row["current_status_raw"]),
            reason_raw=optional(row["reason_raw"]),
            category_raw=optional(row["category_raw"]),
            previous_status_raw=optional(row["previous_status_raw"]),
        ))
    return tuple(listings)


@pytest.mark.parametrize("report_id", REPORT_IDS)
def test_parse_report_matches_the_hand_checked_rows_exactly(report_id) -> None:
    """Would fail if any cell of any row differed from the rows read off the
    rendered pages: a lost continuation line, a wrong carried team, a
    placeholder taken for a player, a page break breaking a group."""
    expected = expected_listings(report_id)
    assert [row.row_number for row in expected] == list(range(1, len(expected) + 1))

    payload = (FIXTURES / f"{report_id}.pdf").read_bytes()
    report = parse.parse_report(payload, report_id=report_id)

    assert isinstance(report, ParsedReport)
    assert report.pages == SOURCES[f"{report_id}.pdf"]["pages"]
    title_text, printed = PRINTED_TITLES[report_id]
    assert report.title_text == title_text
    assert report.title_printed_et == printed
    for actual, wanted in zip(report.listings, expected):
        assert actual == wanted, f"{report_id} row {wanted.row_number}: {actual} != {wanted}"
    assert len(report.listings) == len(expected)
    assert report.listings == expected
    # the same body parses identically without a report id
    assert parse.parse_report(payload) == report


def test_assemble_listings_carries_forward_group_cells_until_replaced() -> None:
    """Would fail if a carried cell leaked past its replacement, failed to
    reach a first player printed on the line below it, or was lost at a
    page break."""
    page_one = [
        (77.0, {**GROUP, **player("Ellenson, Henry", "Out", "Left Ankle Sprain")}),
        (95.0, player("Pachulia, Zaza", "Questionable")),
        (113.0, player("Smith, Ish")),
        (131.0, {"Team": "Milwaukee Bucks", **player("Brogdon, Malcolm", "Probable")}),
        # the next group's cells are printed on the line above its first player
        (149.0, {"Game Time": "07:30 (ET)", "Matchup": "PHX@NYK", "Team": "New York Knicks"}),
        (167.0, player("Dotson, Damyean")),
    ]
    page_two = [
        (77.0, player("Hicks, Isaiah")),
        (95.0, {"Game Date": "12/18/2018", "Game Time": "07:00 (ET)", "Matchup": "CLE@IND",
                "Team": "Indiana Pacers", **player("Evans, Tyreke", "Questionable")}),
        (113.0, player("Reed, Davon", "Doubtful")),
    ]

    listings = parse.assemble_listings([(H2021, page_one), (H2021, page_two)])

    assert [row.row_number for row in listings] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert [row.page for row in listings] == [1, 1, 1, 1, 1, 2, 2, 2]
    assert [row.player_name_raw for row in listings] == [
        "Ellenson, Henry", "Pachulia, Zaza", "Smith, Ish", "Brogdon, Malcolm", "Dotson, Damyean",
        "Hicks, Isaiah", "Evans, Tyreke", "Reed, Davon",
    ]
    assert [row.team_raw for row in listings] == [
        "Detroit Pistons", "Detroit Pistons", "Detroit Pistons", "Milwaukee Bucks",
        "New York Knicks", "New York Knicks", "Indiana Pacers", "Indiana Pacers",
    ]
    assert [row.game_time_raw for row in listings] == ["07:00 (ET)"] * 4 + ["07:30 (ET)"] * 2 + ["07:00 (ET)"] * 2
    assert [row.matchup_raw for row in listings] == ["MIL@DET"] * 4 + ["PHX@NYK"] * 2 + ["CLE@IND"] * 2
    assert [(row.away_tricode, row.home_tricode) for row in listings[4:]] == [("PHX", "NYK")] * 2 + [("CLE", "IND")] * 2
    assert [row.game_date for row in listings] == [date(2018, 12, 17)] * 6 + [date(2018, 12, 18)] * 2
    assert [row.current_status_raw for row in listings] == [
        "Out", "Questionable", "Out", "Probable", "Out", "Out", "Questionable", "Doubtful",
    ]
    assert listings[0].reason_raw == "Left Ankle Sprain"
    assert all(not row.is_placeholder and row.placeholder_raw is None for row in listings)
    assert all(row.category_raw is None and row.previous_status_raw is None for row in listings)
    assert parse.assemble_listings([]) == ()
    assert parse.assemble_listings([(H2021, [])]) == ()


def test_assemble_listings_joins_a_wrapped_team_cell_over_two_lines() -> None:
    """Would fail if the second line of a wrapped team were dropped, taken
    for a new team, or if a team printed alone above a new group's first
    player were glued onto the previous team."""
    # the shape the 2018 fixture prints: the second line carries only Team, 8 pt below
    lines = [
        (473.0, {**GROUP, **player("Roberson, Andre")}),
        (491.0, {"Matchup": "SAC@MIN", "Team": "Minnesota", **player("Bates-Diop, Keita")}),
        (499.0, {"Team": "Timberwolves"}),
        (509.0, player("Bayless, Jerryd")),
    ]
    listings = parse.assemble_listings([(H2021, lines)])
    assert [row.team_raw for row in listings] == [
        "Detroit Pistons", "Minnesota Timberwolves", "Minnesota Timberwolves",
    ]
    assert [row.matchup_raw for row in listings] == ["MIL@DET", "SAC@MIN", "SAC@MIN"]

    # both lines carrying only Team, above the group's first player
    lines = [
        (77.0, {"Game Date": "12/17/2018", "Game Time": "08:00 (ET)", "Matchup": "SAC@MIN"}),
        (95.0, {"Team": "Minnesota"}),
        (103.0, {"Team": "Timberwolves"}),
        (113.0, player("Bates-Diop, Keita")),
    ]
    listings = parse.assemble_listings([(H2021, lines)])
    assert [row.team_raw for row in listings] == ["Minnesota Timberwolves"]

    # a Team alone on the line above a new group's first player, a full row
    # below the previous team, is a new team rather than a continuation
    lines = [
        (77.0, {**GROUP, **player("Roberson, Andre")}),
        (95.0, {"Team": "Minnesota Timberwolves"}),
        (113.0, player("Bates-Diop, Keita")),
    ]
    listings = parse.assemble_listings([(H2021, lines)])
    assert [row.team_raw for row in listings] == ["Detroit Pistons", "Minnesota Timberwolves"]

    # a wrap never crosses a page break
    pages = [
        (H2021, [(77.0, {**GROUP, **player("Roberson, Andre")}), (95.0, {"Team": "Minnesota"})]),
        (H2021, [(77.0, {"Team": "Timberwolves"}), (87.0, player("Bates-Diop, Keita"))]),
    ]
    # page two's "Timberwolves" replaces "Minnesota" instead of joining it
    assert [row.team_raw for row in parse.assemble_listings(pages)] == ["Detroit Pistons", "Timberwolves"]


def test_assemble_listings_attaches_continuation_lines_to_the_nearest_anchor() -> None:
    """Would fail if a reason's continuation line went to the wrong player,
    were joined out of reading order, or were silently dropped."""
    lines = [
        (128.95, {"Game Date": "01/31/2024", "Game Time": "07:00 (ET)", "Matchup": "CHI@CHA",
                  "Team": "Chicago Bulls", **player("Ball, Lonzo", "Out", "Injury/Illness - Left Knee; Surgery")}),
        # GemBox two-line reason: continuation 7 pt above and 7 pt below the row
        (150.98, {"Reason": "Injury/Illness - Right Plantar Fascia;"}),
        (158.00, player("Craig, Torrey", "Out", None)),
        (165.02, {"Reason": "Sprain"}),
        (187.06, player("LaVine, Zach", "Out", "Injury/Illness - Right Ankle; Sprain")),
        # pre-GemBox style: the row's own reason line, then the wrap 4 pt below
        (209.00, player("Murray, Dejounte", "Out", "Torn right anterior cruciate")),
        (213.00, {"Reason": "ligament"}),
        (231.00, player("Walker IV, Lonnie", "Out", "G League - Two-Way")),
        # three text lines: continuation, anchor with its own text, continuation
        (260.00, {"Reason": "Injury/Illness - Bilateral Achilles;"}),
        (267.00, player("Davis, Anthony", "Questionable", "Tendinopathy &")),
        (274.00, {"Reason": "Left Hip Spasm"}),
        # equidistant between two anchors: the anchor above wins
        (300.00, player("James, LeBron", "Questionable", "Injury/Illness - Left Ankle; Peroneal")),
        (310.00, {"Reason": "Tendinopathy"}),
        (320.00, player("Reddish, Cam", "Out", "Injury/Illness - Right Ankle; Sprain")),
    ]
    listings = parse.assemble_listings([(H2021, lines)])
    assert [(row.player_name_raw, row.reason_raw) for row in listings] == [
        ("Ball, Lonzo", "Injury/Illness - Left Knee; Surgery"),
        ("Craig, Torrey", "Injury/Illness - Right Plantar Fascia; Sprain"),
        ("LaVine, Zach", "Injury/Illness - Right Ankle; Sprain"),
        ("Murray, Dejounte", "Torn right anterior cruciate ligament"),
        ("Walker IV, Lonnie", "G League - Two-Way"),
        ("Davis, Anthony", "Injury/Illness - Bilateral Achilles; Tendinopathy & Left Hip Spasm"),
        ("James, LeBron", "Injury/Illness - Left Ankle; Peroneal Tendinopathy"),
        ("Reddish, Cam", "Injury/Illness - Right Ankle; Sprain"),
    ]
    assert [row.row_number for row in listings] == list(range(1, 9))

    # Category wraps the same way in the 2018 layout
    lines = [
        (77.0, {**GROUP, **player("Ellenson, Henry", "Out", "Left Ankle Sprain", **{
            "Category": "Injury/Illness", "Previous Status": "-"})}),
        (95.0, {"Category": "Personal", **player("Beasley, Michael", "Out", "-", **{"Previous Status": "-"})}),
        (99.0, {"Category": "Reasons"}),
    ]
    listings = parse.assemble_listings([(H2018, lines)])
    assert [(row.category_raw, row.reason_raw, row.previous_status_raw) for row in listings] == [
        ("Injury/Illness", "Left Ankle Sprain", "-"), ("Personal Reasons", "-", "-"),
    ]

    # a continuation line on a page without any anchor has nothing to attach to
    with pytest.raises(ReportParseError, match="continuation line without an anchor"):
        parse.assemble_listings([(H2021, [(77.0, {**GROUP}), (85.0, {"Reason": "Sprain"})])])
    # a continuation line attached to a placeholder would be lost
    with pytest.raises(ReportParseError, match="continuation line attached to a placeholder"):
        parse.assemble_listings([(H2021, [
            (77.0, {**GROUP, "Reason": "NOT YET SUBMITTED"}), (84.0, {"Reason": "Sprain"}),
        ])])


def test_assemble_listings_recognises_each_placeholder_kind() -> None:
    """Would fail if a placeholder were taken for a player row, kept its '-'
    and ',' cells, or if one phrase's column placement went unrecognised."""
    lines_2018 = [
        (77.0, {**GROUP, **player("Ellenson, Henry", "Out", "Left Ankle Sprain",
                                  **{"Category": "Injury/Illness", "Previous Status": "-"})}),
        (95.0, {"Matchup": "POR@LAC", "Team": "Portland Trail Blazers", "Category": "ALL PLAYERS AVAILABLE"}),
        (113.0, {"Game Date": "12/18/2018", "Game Time": "07:00 (ET)", "Matchup": "CLE@IND",
                 "Team": "Cleveland Cavaliers", "Category": "NOT YET SUBMITTED"}),
        (131.0, {"Game Time": "09:00 (ET)", "Matchup": "UNK@UNK", "Team": "Non-NBA Team", "Player Name": ",",
                 "Category": "-", "Reason": "-", "Previous Status": "-"}),
        (149.0, {"Player Name": ",", "Category": "-", "Reason": "-", "Previous Status": "-"}),
        (167.0, {"Matchup": "UNK@HOU", "Team": "Houston Rockets",
                 **player("Qi, Zhou", "Probable", "Left Ankle Sprain", **{"Category": "Injury/Illness", "Previous Status": "-"})}),
    ]
    listings = parse.assemble_listings([(H2018, lines_2018)])
    assert [(row.row_number, row.is_placeholder, row.placeholder_raw) for row in listings] == [
        (1, False, None), (2, True, "ALL PLAYERS AVAILABLE"), (3, True, "NOT YET SUBMITTED"),
        (4, True, "Non-NBA Team"), (5, True, "Non-NBA Team"), (6, False, None),
    ]
    for row in listings[1:5]:
        assert row.player_name_raw is None and row.current_status_raw is None
        assert row.reason_raw is None and row.category_raw is None and row.previous_status_raw is None
    assert listings[1].team_raw == "Portland Trail Blazers" and listings[1].matchup_raw == "POR@LAC"
    assert listings[1].game_date == date(2018, 12, 17) and listings[1].game_time_raw == "07:00 (ET)"
    assert listings[2].game_date == date(2018, 12, 18) and listings[2].team_raw == "Cleveland Cavaliers"
    assert (listings[3].matchup_raw, listings[3].away_tricode, listings[3].home_tricode) == ("UNK@UNK", "UNK", "UNK")
    assert listings[4].team_raw == "Non-NBA Team" and listings[4].game_time_raw == "09:00 (ET)"
    assert listings[5] == Listing(
        page=1, row_number=6, game_date=date(2018, 12, 18), game_time_raw="09:00 (ET)", matchup_raw="UNK@HOU",
        away_tricode="UNK", home_tricode="HOU", team_raw="Houston Rockets", is_placeholder=False,
        placeholder_raw=None, player_name_raw="Qi, Zhou", current_status_raw="Probable",
        reason_raw="Left Ankle Sprain", category_raw="Injury/Illness", previous_status_raw="-",
    )

    # from 2020-21 on the phrase lands in Reason
    lines_2021 = [
        (77.0, {"Game Date": "04/02/2021", "Game Time": "09:00 (ET)", "Matchup": "ATL@NOP",
                "Team": "Atlanta Hawks", "Reason": "NOT YET SUBMITTED"}),
        (95.0, {"Team": "New Orleans Pelicans", "Reason": "NOT YET SUBMITTED"}),
        (113.0, {"Player Name": "NOT YET SUBMITTED"}),
    ]
    listings = parse.assemble_listings([(H2021, lines_2021)])
    assert [(row.team_raw, row.placeholder_raw) for row in listings] == [
        ("Atlanta Hawks", "NOT YET SUBMITTED"), ("New Orleans Pelicans", "NOT YET SUBMITTED"),
        ("New Orleans Pelicans", "NOT YET SUBMITTED"),
    ]
    assert all(row.is_placeholder for row in listings)

    # a placeholder line that also carries player cells is not a placeholder
    with pytest.raises(ReportParseError, match="unknown anchor"):
        parse.assemble_listings([(H2021, [(77.0, {**GROUP, "Reason": "NOT YET SUBMITTED", "Current Status": "Out"})])])


def test_assemble_listings_fails_on_an_unknown_anchor_without_a_player_name() -> None:
    first = (77.0, {**GROUP, **player("Ellenson, Henry")})
    # a status without a player name
    with pytest.raises(ReportParseError, match=r"report: unknown anchor without a player name \(page 1"):
        parse.assemble_listings([(H2021, [first, (95.0, {"Current Status": "Out", "Reason": "Illness"})])])
    with pytest.raises(ReportParseError, match="unknown anchor without a player name"):
        parse.assemble_listings([(H2018, [first, (95.0, {"Previous Status": "Available"})])])
    # the ',' cell marks a Non-NBA Team row only under that team string
    with pytest.raises(ReportParseError, match="unknown anchor without a player name"):
        parse.assemble_listings([(H2018, [first, (95.0, {"Player Name": ",", "Category": "-", "Reason": "-"})])])
    # an unknown phrase in the placeholder position is not a placeholder
    with pytest.raises(ReportParseError, match="unknown anchor without a player name"):
        parse.assemble_listings([(H2021, [first, (95.0, {"Team": "Denver Nuggets", "Current Status": "Out"})])])
    # the report id prefixes the message
    with pytest.raises(ReportParseError, match=r"^Injury-Report_2018-12-17_05PM: unknown anchor"):
        parse.assemble_listings(
            [(H2021, [first, (95.0, {"Current Status": "Out"})])], report_id="Injury-Report_2018-12-17_05PM",
        )


class FakePdf:
    def __init__(self, pages) -> None:
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


TITLE_WORDS = [
    word("Injury", 300.0, 20.0), word("Report:", 330.0, 20.0), word("12/17/18", 370.0, 20.0),
    word("05:30", 410.0, 20.0), word("PM", 440.0, 20.0),
]


def footer_words(i: int, n: int, top: float = 573.66) -> list[dict]:
    return [word("Page", 400.0, top), word(str(i), 420.9, top), word("of", 427.58, top), word(str(n), 436.47, top)]


def row_words(cells: dict[str, str], top: float) -> list[dict]:
    """Words for one 2018-layout text line, each cell starting at its column's left edge."""
    lefts = dict(H2018.columns)
    words = []
    for label, text in cells.items():
        x0 = lefts[label] - 1.0
        for piece in text.split(" "):
            words.append(word(piece, x0, top))
            x0 += 4.0 * len(piece) + 3.0
    return words


ROW_2018 = {
    "Game Date": "12/17/2018", "Game Time": "07:00 (ET)", "Matchup": "MIL@DET", "Team": "Detroit Pistons",
    "Player Name": "Ellenson, Henry", "Category": "Injury/Illness", "Reason": "Left Ankle Sprain",
    "Current Status": "Out", "Previous Status": "-",
}


def synthetic_pages(mutation: str) -> tuple[list[list[dict]], str]:
    """One-page (or two-page) 2018-layout reports, each with exactly one defect."""
    row = dict(ROW_2018)
    pages_total = 1
    title = TITLE_WORDS
    header_two = header_words()
    if mutation == "bad_game_date":
        row["Game Date"] = "12/17/18"
        gate = "game date"
    elif mutation == "bad_game_time":
        row["Game Time"] = "7:00 (ET)"
        gate = "game time"
    elif mutation == "bad_matchup":
        row["Matchup"] = "MIL@DETX"
        gate = "matchup"
    elif mutation == "unknown_status":
        row["Current Status"] = "Inactive"
        gate = "status"
    elif mutation == "missing_team":
        del row["Team"]
        gate = "carried cell missing: Team"
    elif mutation == "footer_count_mismatch":
        pages_total = 2
        gate = "footer"
    elif mutation == "header_columns_differ":
        header_two = [w for w in header_words() if w["text"] not in {"Category"}]
        gate = "header"
    elif mutation == "title_unparsed":
        title = [word("Injury", 300.0, 20.0), word("Report", 330.0, 20.0)]
        gate = "title"
    elif mutation == "stray_word":
        gate = "word left of the first column"
    else:
        raise AssertionError(mutation)
    page_one = title + header_words() + row_words(row, 77.0) + footer_words(1, pages_total)
    if mutation == "stray_word":
        page_one = page_one + [word("stray", 2.0, 95.0)]
    pages = [page_one]
    if mutation == "header_columns_differ":
        pages.append(title + header_two + row_words({"Player Name": "Pachulia, Zaza", "Current Status": "Out"}, 77.0)
                     + footer_words(2, 2))
        pages[0] = title + header_words() + row_words(row, 77.0) + footer_words(1, 2)
    return pages, gate


@pytest.mark.parametrize(
    "mutation",
    ["bad_game_date", "bad_game_time", "bad_matchup", "unknown_status", "missing_team",
     "footer_count_mismatch", "header_columns_differ", "title_unparsed", "stray_word"],
)
def test_parse_report_gates_fail_closed(mutation, monkeypatch) -> None:
    """Would fail if a malformed cell, a page count disagreement, a changed
    header or an unparsed title produced listings instead of an error."""
    pages, gate = synthetic_pages(mutation)
    fake_pages = [FakePage(words) for words in pages]
    monkeypatch.setattr(parse.pdfplumber, "open", lambda handle: FakePdf(fake_pages))

    with pytest.raises(ReportParseError, match=f"^Injury-Report_2018-12-17_05PM: .*{gate}") as info:
        parse.parse_report(b"%PDF-synthetic", report_id="Injury-Report_2018-12-17_05PM")
    assert isinstance(info.value, ValueError)
    with pytest.raises(ReportParseError, match=f"^report: .*{gate}"):
        parse.parse_report(b"%PDF-synthetic")

    # the untouched page parses, so each failure is the mutation's own
    good, _ = synthetic_pages("footer_count_mismatch")
    good_page = FakePage(good[0][:-4] + footer_words(1, 1))
    monkeypatch.setattr(parse.pdfplumber, "open", lambda handle: FakePdf([good_page]))
    report = parse.parse_report(b"%PDF-synthetic", report_id="Injury-Report_2018-12-17_05PM")
    assert report.pages == 1 and report.title_printed_et == datetime(2018, 12, 17, 17, 30)
    assert report.title_text == "Injury Report: 12/17/18 05:30 PM"
    assert [row.player_name_raw for row in report.listings] == ["Ellenson, Henry"]
    assert report.listings[0].reason_raw == "Left Ankle Sprain"

    # the cell gates also run inside assemble_listings on their own
    if mutation in {"bad_game_date", "bad_game_time", "bad_matchup", "unknown_status", "missing_team"}:
        row = dict(ROW_2018)
        if mutation == "bad_game_date":
            row["Game Date"] = "12/17/18"
        elif mutation == "bad_game_time":
            row["Game Time"] = "7:00 (ET)"
        elif mutation == "bad_matchup":
            row["Matchup"] = "MIL@DETX"
        elif mutation == "unknown_status":
            row["Current Status"] = "Inactive"
        else:
            del row["Team"]
        with pytest.raises(ReportParseError, match=f"^report: .*{gate}"):
            parse.assemble_listings([(H2018, [(77.0, row)])])
    # a date that matches the pattern but is not a calendar date fails the same gate
    if mutation == "bad_game_date":
        row = {**ROW_2018, "Game Date": "13/45/2018"}
        with pytest.raises(ReportParseError, match="game date"):
            parse.assemble_listings([(H2018, [(77.0, row)])])
    # a player row without any status fails the status gate
    if mutation == "unknown_status":
        row = dict(ROW_2018)
        del row["Current Status"]
        with pytest.raises(ReportParseError, match="status"):
            parse.assemble_listings([(H2018, [(77.0, row)])])


def test_parse_report_rejects_a_body_that_does_not_open() -> None:
    with pytest.raises(ReportParseError, match=r"^report: body does not open") as info:
        parse.parse_report(b"not a pdf at all")
    assert isinstance(info.value, ValueError)
    with pytest.raises(ReportParseError, match=r"^Injury-Report_2024-01-31_05PM: body does not open"):
        parse.parse_report(b"", report_id="Injury-Report_2024-01-31_05PM")
    with pytest.raises(ReportParseError, match="body does not open"):
        parse.parse_report(b"%PDF-1.4\n%%EOF\n")
    # a truncated real body does not open either
    payload = (FIXTURES / "Injury-Report_2019-02-15_01PM.pdf").read_bytes()
    with pytest.raises(ReportParseError, match="body does not open"):
        parse.parse_report(payload[: len(payload) // 3])
