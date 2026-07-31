"""Tests for the fixed V3 play-by-play event contract parser."""

from datetime import date
import json
from pathlib import Path

import pytest

from nba_data import pbp_build


FIXTURES = Path(__file__).resolve().parent / "fixtures/pbp"


@pytest.mark.parametrize(
    ("clock", "period", "expected"),
    [
        ("PT11M35.00S", 1, 695.0),
        ("PT00M07.30S", 4, 7.3),
        ("PT00M00.00S", 4, 0.0),
        ("PT05M00.00S", 5, 300.0),
    ],
)
def test_parse_clock(clock, period, expected):
    assert pbp_build.parse_clock(clock, period) == expected


@pytest.mark.parametrize(
    ("clock", "period"),
    [
        ("11:35", 1),
        ("PT12M00.01S", 5),
        ("PT05M00.01S", 6),
        ("PT-1M00S", 1),
    ],
)
def test_parse_clock_rejects_invalid_values(clock, period):
    with pytest.raises(pbp_build.PBPBuildError, match="invalid V3 clock"):
        pbp_build.parse_clock(clock, period)


def test_running_scores_preserve_early_zero_sentinel_and_correction():
    home, away = pbp_build.derive_running_scores(
        ["0", "0", "0", "98", "98"],
        ["0", "2", "0", "108", "107"],
    )
    assert home == [0, 0, 0, 98, 98]
    assert away == [0, 2, 2, 108, 107]


def test_running_scores_carry_modern_blanks():
    home, away = pbp_build.derive_running_scores(
        ["0", "", "2"],
        ["0", "", "0"],
    )
    assert home == [0, 0, 2]
    assert away == [0, 0, 0]


@pytest.mark.parametrize(
    ("home_raw", "away_raw"),
    [
        (["0", ""], ["0", "5"]),  # one-sided blank (home blank, away not)
        (["0", "5"], ["0", ""]),  # one-sided blank (away blank, home not)
        (["0", "-5"], ["0", "3"]),  # negative score
        (["0", "12.5"], ["0", "9"]),  # non-integer score
        (["5"], ["3"]),  # first valid pair other than 0-0
    ],
    ids=[
        "one-sided-blank-home",
        "one-sided-blank-away",
        "negative-score",
        "non-integer-score",
        "first-pair-not-zero-zero",
    ],
)
def test_running_scores_reject_invalid_pairs(home_raw, away_raw):
    with pytest.raises(pbp_build.PBPBuildError, match="invalid raw score pair"):
        pbp_build.derive_running_scores(home_raw, away_raw)


def worklist_row(**changes):
    row = {
        "game_id": "0029600001",
        "season": 1997,
        "game_type": "regular",
        "game_date": date(1996, 11, 1),
        "away_franchise_id": "CHI",
        "home_franchise_id": "BOS",
        "away_pts": 107,
        "home_pts": 98,
    }
    row.update(changes)
    return row


def test_parse_game_uses_action_id_primary_key_and_preserves_action_number():
    frame, report = pbp_build.parse_game_payload(
        (FIXTURES / "early_score_sentinel.json").read_bytes(),
        worklist_row(),
    )
    assert frame["action_id"].tolist() == list(range(1, 9))
    assert frame["action_number"].tolist().count(10) == 2
    assert frame[["score_home", "score_away"]].iloc[-1].tolist() == [98, 107]
    assert frame.columns.tolist() == pbp_build.EVENT_COLUMNS
    assert report["events"] == 8
    assert report["historical_zero_sentinels"] == 2


def test_parse_game_accepts_modern_blanks_and_double_overtime():
    frame, report = pbp_build.parse_game_payload(
        (FIXTURES / "modern_blank_score.json").read_bytes(),
        worklist_row(
            game_id="0022500001",
            season=2026,
            game_date=date(2025, 10, 21),
            away_franchise_id="HOU",
            home_franchise_id="OKC",
            away_pts=124,
            home_pts=125,
        ),
    )
    assert frame["period"].unique().tolist() == [1, 2, 3, 4, 5, 6]
    assert frame.loc[1, ["score_home", "score_away"]].tolist() == [0, 0]
    assert report["max_period"] == 6


def test_parse_game_preserves_exact_raw_score_strings():
    # Global constraint: "Preserve the exact source score strings in
    # score_home_raw and score_away_raw; derive non-null running scores
    # separately." This must hold even where the derived score_home/
    # score_away differ from the raw string (historical zero sentinels)
    # or where the raw string is blank (modern carried-forward blanks).
    sentinel_frame, _ = pbp_build.parse_game_payload(
        (FIXTURES / "early_score_sentinel.json").read_bytes(),
        worklist_row(),
    )
    assert sentinel_frame["score_home_raw"].tolist() == [
        "0", "0", "0", "0", "25", "50", "98", "98",
    ]
    assert sentinel_frame["score_away_raw"].tolist() == [
        "0", "2", "0", "0", "30", "60", "108", "107",
    ]

    blank_frame, _ = pbp_build.parse_game_payload(
        (FIXTURES / "modern_blank_score.json").read_bytes(),
        worklist_row(
            game_id="0022500001",
            season=2026,
            game_date=date(2025, 10, 21),
            away_franchise_id="HOU",
            home_franchise_id="OKC",
            away_pts=124,
            home_pts=125,
        ),
    )
    assert blank_frame["score_home_raw"].tolist() == [
        "0", "", "30", "60", "100", "112", "125",
    ]
    assert blank_frame["score_away_raw"].tolist() == [
        "0", "", "32", "61", "100", "112", "124",
    ]
    # The blank pair must stay the empty string, not be cast to NaN/null
    # by the nullable "string" dtype.
    assert blank_frame.loc[1, "score_home_raw"] == ""
    assert blank_frame.loc[1, "score_away_raw"] == ""
    assert blank_frame["score_home_raw"].notna().all()
    assert blank_frame["score_away_raw"].notna().all()


def _document_bytes(mutator):
    document = json.loads(
        (FIXTURES / "early_score_sentinel.json").read_bytes()
    )
    mutator(document)
    return json.dumps(document).encode("utf-8")


def _wrong_outer_game_id(document):
    document["game"]["gameId"] = "0000000000"


def _missing_action_key(document):
    del document["game"]["actions"][0]["location"]


def _additional_action_key(document):
    document["game"]["actions"][0]["extraKey"] = 1


def _boolean_in_integer_field(document):
    document["game"]["actions"][0]["actionNumber"] = True


def _non_string_in_string_field(document):
    document["game"]["actions"][0]["actionType"] = 5


def _duplicate_action_id(document):
    document["game"]["actions"][1]["actionId"] = (
        document["game"]["actions"][0]["actionId"]
    )


def _zero_action_id(document):
    document["game"]["actions"][0]["actionId"] = 0


def _out_of_order_action_id(document):
    actions = document["game"]["actions"]
    actions[0]["actionId"], actions[1]["actionId"] = (
        actions[1]["actionId"],
        actions[0]["actionId"],
    )


def _period_gap(document):
    # Row index 4 is the period-2 boundary action; skipping straight to 3
    # breaks the required 1..max(period) coverage.
    document["game"]["actions"][4]["period"] = 3


def _terminal_score_mismatch(document):
    document["game"]["actions"][-1]["scoreHome"] = "99"


@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (_wrong_outer_game_id, "gameId"),
        (_missing_action_key, "missing key"),
        (_additional_action_key, "unexpected key"),
        (_boolean_in_integer_field, "non-boolean integer"),
        (_non_string_in_string_field, "string or null"),
        (_duplicate_action_id, "duplicate actionId"),
        (_zero_action_id, "actionId must be positive"),
        (_out_of_order_action_id, "actionId must strictly increase"),
        (_period_gap, "periods must run"),
        (_terminal_score_mismatch, "terminal score"),
    ],
    ids=[
        "wrong-outer-game-id",
        "missing-action-key",
        "additional-action-key",
        "boolean-in-integer-field",
        "non-string-in-string-field",
        "duplicate-action-id",
        "zero-negative-action-id",
        "out-of-order-action-id",
        "period-coverage-gap",
        "terminal-score-mismatch",
    ],
)
def test_parse_game_payload_rejects_contract_violations(mutator, match):
    with pytest.raises(pbp_build.PBPBuildError, match=match):
        pbp_build.parse_game_payload(_document_bytes(mutator), worklist_row())
