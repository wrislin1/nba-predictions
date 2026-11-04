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
