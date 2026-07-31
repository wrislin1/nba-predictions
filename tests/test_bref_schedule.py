import datetime
from pathlib import Path

import pandas as pd
import pytest

from nba_data import bref_schedule


FIXTURE = Path(__file__).parent / "fixtures/playoff_bref/NBA_1984_games.html"
HISTORICAL_FIXTURES = Path(__file__).parent / "fixtures/historical_bref"
PLAYOFF_URL = (
    "https://www.basketball-reference.com/playoffs/NBA_1984_games.html"
)
HISTORICAL_URL = (
    "https://www.basketball-reference.com/leagues/"
    "BAA_1947_games-november.html"
)


def test_parse_schedule_page_accepts_explicit_league():
    """Would fail if the parser inferred a regular-season league boundary."""
    frame = bref_schedule.parse_schedule_page(
        FIXTURE.read_bytes(),
        season=1984,
        league="NBA",
        schedule_url=PLAYOFF_URL,
    )

    row = frame.iloc[0]
    assert row["game_id"] == "bref-198404170LAL"
    assert row["league"] == "NBA"
    assert row["game_date"].isoformat() == "1984-04-17"
    assert row["away_pts"] == 113
    assert row["home_pts"] == 116
    assert row["overtime_periods"] == 0
    assert row["attendance"] == 15298
    assert row["attendance_status"] == "reported_positive"


@pytest.mark.parametrize(
    ("marker", "expected"),
    [("", 0), ("OT", 1), ("2OT", 2), ("5OT", 5)],
)
def test_overtime_periods(marker, expected):
    """Would fail if an overtime marker parsed to the wrong number of periods."""
    assert bref_schedule.overtime_periods(marker) == expected


def test_overtime_periods_rejects_unknown_marker():
    """Would fail if non-overtime schedule remarks were accepted as evidence."""
    with pytest.raises(ValueError, match="unsupported overtime marker"):
        bref_schedule.overtime_periods("postponed")


def test_parser_preserves_missing_zero_and_overtime_semantics():
    """Would fail if source missingness, reported zero, or OT were conflated."""
    result = bref_schedule.parse_schedule_page(
        (HISTORICAL_FIXTURES / "BAA_1947_games-november.html").read_bytes(),
        season=1947,
        league="BAA",
        schedule_url=HISTORICAL_URL,
    )

    assert pd.isna(result.iloc[0]["attendance"])
    assert result.iloc[1]["attendance"] == 4329
    assert result.iloc[2]["attendance"] == 0
    assert pd.isna(result.iloc[2]["venue_name"])
    assert result["attendance_status"].tolist() == [
        "missing", "reported_positive", "reported_zero"
    ]
    assert result["overtime_periods"].tolist() == [0, 1, 0]
    assert result["site_status"].tolist() == ["home", "home", "unknown"]
    assert result.iloc[0]["game_id"] == "bref-194611010TRH"
    assert result.iloc[2]["game_id"].startswith(
        "bref-fallback-19461112-boston-celtics-at-providence-steamrollers-"
    )


def test_parser_accepts_only_the_explicit_neutral_marker():
    """Would fail if an undocumented game remark were classified as neutral."""
    result = bref_schedule.parse_schedule_page(
        (HISTORICAL_FIXTURES / "NBA_1960_games-october.html").read_bytes(),
        season=1960,
        league="NBA",
        schedule_url=(
            "https://www.basketball-reference.com/leagues/"
            "NBA_1960_games-october.html"
        ),
    )

    assert result["site_status"].tolist() == ["home", "neutral"]


@pytest.mark.parametrize(
    "href",
    [
        "",
        "   ",
        "https://attacker.invalid/boxscores/194611010TRH.html",
        "/games/194611010TRH.html",
        "/boxscores/19461101TRH.html",
    ],
)
def test_parser_rejects_present_invalid_boxscore_href(href):
    """Would fail if malformed present box-score links silently became fallback IDs."""
    payload = (HISTORICAL_FIXTURES / "BAA_1947_games-november.html").read_text(
        encoding="utf-8"
    )
    payload = payload.replace("/boxscores/194611010TRH.html", href, 1)

    with pytest.raises(ValueError, match="invalid box-score href"):
        bref_schedule.parse_schedule_page(
            payload.encode(),
            season=1947,
            league="BAA",
            schedule_url=HISTORICAL_URL,
        )


def test_parser_allows_absent_boxscore_href_with_a_stable_fallback_id():
    """Would fail if pages without a box score could not retain an identity."""
    result = bref_schedule.parse_schedule_page(
        (HISTORICAL_FIXTURES / "BAA_1947_games-november.html").read_bytes(),
        season=1947,
        league="BAA",
        schedule_url=HISTORICAL_URL,
    )

    assert pd.isna(result.iloc[2]["boxscore_url"])
    assert result.iloc[2]["game_id"].startswith("bref-fallback-")


def test_assign_game_ids_is_stable_across_input_order():
    """Would fail if fallback duplicate ordinals depended on incoming row order."""
    records = pd.DataFrame({
        "game_date": [datetime.date(1946, 11, 12)] * 2,
        "schedule_url": ["https://example.test/b", "https://example.test/a"],
        "source_row": [1, 1],
        "away_team_name": ["Boston Celtics"] * 2,
        "home_team_name": ["Providence Steamrollers"] * 2,
        "boxscore_slug": [pd.NA, pd.NA],
        "attendance": [pd.NA, pd.NA],
        "league": ["BAA"] * 2,
        "overtime_marker": [""] * 2,
        "site_status": ["unknown"] * 2,
        "venue_name": [pd.NA, pd.NA],
        "attendance_status": ["missing"] * 2,
        "boxscore_url": [pd.NA, pd.NA],
        "game_remarks": [""] * 2,
    })

    forward = bref_schedule.assign_game_ids(records)
    reverse = bref_schedule.assign_game_ids(records.iloc[::-1])

    assert forward[["schedule_url", "game_id"]].equals(
        reverse[["schedule_url", "game_id"]]
    )
    assert forward["game_id"].is_unique
    assert forward["game_id"].tolist() == [
        "bref-fallback-19461112-boston-celtics-at-providence-steamrollers-01",
        "bref-fallback-19461112-boston-celtics-at-providence-steamrollers-02",
    ]


def test_parser_skips_non_final_rows_with_no_scores():
    """Would fail if a future non-final schedule row became a malformed game."""
    payload = (HISTORICAL_FIXTURES / "BAA_1947_games-november.html").read_text()
    non_final_row = """
    <tr>
      <th data-stat="date_game">Wed, Nov 13, 1946</th>
      <td data-stat="visitor_team_name">Boston Celtics</td>
      <td data-stat="visitor_pts"></td>
      <td data-stat="home_team_name">New York Knicks</td>
      <td data-stat="home_pts"></td>
      <td data-stat="box_score_text"></td>
      <td data-stat="overtimes"></td>
    </tr>
    """
    payload = payload.replace("  </tbody>", non_final_row + "  </tbody>")

    result = bref_schedule.parse_schedule_page(
        payload.encode(), season=1947, league="BAA", schedule_url=HISTORICAL_URL
    )

    assert len(result) == 3


def test_parser_rejects_duplicate_schedule_tables():
    """Would fail if an ambiguous page selected an arbitrary schedule table."""
    payload = FIXTURE.read_text()
    payload = payload.replace("</table>", "</table>" + payload, 1)

    with pytest.raises(ValueError, match="expected one schedule table, found 2"):
        bref_schedule.parse_schedule_page(
            payload.encode(), season=1984, league="NBA", schedule_url=PLAYOFF_URL
        )


def test_parser_rejects_unsupported_league():
    """Would fail if an invalid league entered emitted schedule data."""
    with pytest.raises(ValueError, match="unsupported league: WNBA"):
        bref_schedule.parse_schedule_page(
            FIXTURE.read_bytes(),
            season=1984,
            league="WNBA",
            schedule_url=PLAYOFF_URL,
        )
