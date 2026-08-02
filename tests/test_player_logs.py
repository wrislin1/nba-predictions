"""Tests for exact NBA player game-log normalization."""

from __future__ import annotations

import json

import pytest

from nba_data import player_logs


HEADERS = [
    "SEASON_ID", "PLAYER_ID", "PLAYER_NAME", "TEAM_ID",
    "TEAM_ABBREVIATION", "TEAM_NAME", "GAME_ID", "GAME_DATE",
    "MATCHUP", "WL", "MIN", "FGM", "FGA", "FG_PCT", "FG3M",
    "FG3A", "FG3_PCT", "FTM", "FTA", "FT_PCT", "OREB", "DREB",
    "REB", "AST", "STL", "BLK", "TOV", "PF", "PTS", "PLUS_MINUS",
    "FANTASY_PTS", "VIDEO_AVAILABLE",
]


def _row(
    player_id: int,
    player_name: str,
    team_id: int,
    abbreviation: str,
    team_name: str,
    matchup: str,
    minutes: int,
    points: int,
) -> list[object]:
    return [
        "21983", player_id, player_name, team_id, abbreviation, team_name,
        "0028300001", "1983-10-28", matchup, "W" if abbreviation == "BOS" else "L",
        minutes, 1 if points else 0, 2 if points else 0, 0.5 if points else 0.0,
        0, 0, 0.0, 0, 0, 0.0, 0, 1 if minutes else 0,
        1 if minutes else 0, 1 if minutes else 0, 0, 0, 0, 1 if minutes else 0,
        points, None, 0.0, 0,
    ]


REGULAR_ROWS = [
    _row(1, "Chicago One", 1610612741, "CHI", "Chicago Bulls", "CHI @ BOS", 48, 2),
    _row(2, "Chicago Zero", 1610612741, "CHI", "Chicago Bulls", "CHI @ BOS", 0, 0),
    _row(3, "Boston One", 1610612738, "BOS", "Boston Celtics", "BOS vs. CHI", 48, 2),
    _row(4, "Boston Two", 1610612738, "BOS", "Boston Celtics", "BOS vs. CHI", 24, 2),
]


def _payload(rows: list[list[object]], headers=HEADERS) -> bytes:
    return json.dumps({
        "resultSets": [{
            "name": "LeagueGameLog",
            "headers": list(headers),
            "rowSet": rows,
        }]
    }).encode("utf-8")


def test_normalize_payload_preserves_source_and_nullable_semantics():
    frame = player_logs.normalize_payload(
        _payload(REGULAR_ROWS), season=1984, game_type="regular"
    )
    assert frame.columns.tolist() == player_logs.PLAYER_GAME_COLUMNS
    assert frame["game_id"].unique().tolist() == ["0028300001"]
    assert frame["person_id"].dtype == "int64"
    assert frame.loc[frame.person_id.eq(2), "min"].item() == 0
    assert frame["plus_minus"].isna().all()
    assert set(frame.loc[frame.is_home, "franchise_id"]) == {"BOS"}
    assert set(frame.loc[~frame.is_home, "franchise_id"]) == {"CHI"}
    assert set(frame.loc[frame.franchise_id.eq("BOS"), "opponent_franchise_id"]) == {"CHI"}


def _document(rows=REGULAR_ROWS, headers=HEADERS, *, result_sets=None):
    return {
        "resultSets": result_sets if result_sets is not None else [{
            "name": "LeagueGameLog",
            "headers": list(headers),
            "rowSet": [list(row) for row in rows],
        }]
    }


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (b"{", "invalid player-log JSON"),
        (json.dumps([]).encode(), "must be a JSON object"),
        (json.dumps({}).encode(), "missing resultSets"),
        (_payload(REGULAR_ROWS, HEADERS + ["MIN"]), "duplicate header"),
        (_payload(REGULAR_ROWS, [header for header in HEADERS if header != "PTS"]), "missing required header"),
        (json.dumps(_document(result_sets=[{
            "name": "Other", "headers": HEADERS, "rowSet": REGULAR_ROWS,
        }])).encode(), "expected exactly one 'LeagueGameLog' result set"),
        (json.dumps(_document(result_sets=[
            {"name": "LeagueGameLog", "headers": HEADERS, "rowSet": REGULAR_ROWS},
            {"name": "LeagueGameLog", "headers": HEADERS, "rowSet": REGULAR_ROWS},
        ])).encode(), "expected exactly one 'LeagueGameLog' result set"),
        (json.dumps(_document(result_sets=[{
            "name": "LeagueGameLog", "headers": HEADERS, "rowSet": {},
        }])).encode(), "rowSet must be a list"),
        (_payload([REGULAR_ROWS[0][:-1]]), "has width 31, expected row width 32"),
    ],
)
def test_validate_payload_bytes_rejects_invalid_source_envelopes(payload, match):
    with pytest.raises(ValueError, match=match):
        player_logs.validate_payload_bytes(payload)


def test_validate_payload_bytes_rejects_an_extra_wrong_result_set():
    payload = json.dumps(_document(result_sets=[
        {"name": "Other", "headers": HEADERS, "rowSet": REGULAR_ROWS},
        {"name": "LeagueGameLog", "headers": HEADERS, "rowSet": REGULAR_ROWS},
    ])).encode()
    with pytest.raises(ValueError, match="expected exactly one 'LeagueGameLog' result set"):
        player_logs.validate_payload_bytes(payload)


def test_source_helpers_define_season_scope_cache_location_and_file_validation(tmp_path):
    assert player_logs.season_label(1984) == "1983-84"
    assert player_logs.applicable_game_types(1984) == ("regular", "playoff")
    assert player_logs.applicable_game_types(2020) == (
        "regular", "playoff", "play_in"
    )
    assert player_logs.cache_path(tmp_path, 1984, "regular") == (
        tmp_path / "raw/stats_nba/player_logs/season=1984/regular.json"
    )
    payload_path = tmp_path / "source.json"
    payload_path.write_bytes(_payload(REGULAR_ROWS))
    assert player_logs.validate_payload(payload_path) is None


@pytest.mark.parametrize("season", [1983, 2027])
def test_source_helpers_reject_seasons_outside_the_publication_range(season):
    with pytest.raises(ValueError, match="1984..2026"):
        player_logs.season_label(season)


def _rows_with(field: str, value: object) -> list[list[object]]:
    rows = [list(row) for row in REGULAR_ROWS]
    rows[0][HEADERS.index(field)] = value
    return rows


@pytest.mark.parametrize(
    ("payload", "season", "game_type", "match"),
    [
        (_payload(REGULAR_ROWS), 1984, "exhibition", "unknown game type"),
        (_payload(REGULAR_ROWS), 1983, "regular", "1984..2026"),
        (_payload(_rows_with("SEASON_ID", "21982")), 1984, "regular", "ending season mismatch"),
        (_payload(_rows_with("GAME_ID", "0048300001")), 1984, "regular", "GAME_ID prefix mismatch"),
        (_payload(_rows_with("MATCHUP", "CHI - BOS")), 1984, "regular", "MATCHUP does not match"),
        (_payload(_rows_with("TEAM_ABBREVIATION", "NYK")), 1984, "regular", "exactly one side"),
        (_payload(_rows_with("MATCHUP", "CHI @ CHI")), 1984, "regular", "exactly one side"),
        (_payload(_rows_with("PLAYER_ID", 1.5)), 1984, "regular", "PLAYER_ID must be an exact integer"),
        (_payload(_rows_with("PTS", 1.5)), 1984, "regular", "PTS must be an exact integer"),
        (_payload(_rows_with("PTS", -1)), 1984, "regular", "PTS must be non-negative"),
        (_payload(_rows_with("PLAYER_NAME", None)), 1984, "regular", "PLAYER_NAME must not be null"),
    ],
)
def test_normalize_payload_rejects_invalid_source_values(
    payload, season, game_type, match
):
    with pytest.raises(ValueError, match=match):
        player_logs.normalize_payload(payload, season=season, game_type=game_type)


def test_normalize_payload_rejects_games_with_one_or_three_team_identities():
    one_team_rows = [list(row) for row in REGULAR_ROWS]
    for row in one_team_rows:
        if row[HEADERS.index("TEAM_ABBREVIATION")] == "BOS":
            row[HEADERS.index("TEAM_ID")] = 1610612741
            row[HEADERS.index("TEAM_NAME")] = "Chicago Bulls"
            row[HEADERS.index("TEAM_ABBREVIATION")] = "CHI"
            row[HEADERS.index("MATCHUP")] = "CHI @ BOS"
    three_team_rows = [list(row) for row in REGULAR_ROWS]
    third = list(three_team_rows[0])
    third[HEADERS.index("PLAYER_ID")] = 5
    third[HEADERS.index("TEAM_ID")] = 1610612752
    third[HEADERS.index("TEAM_ABBREVIATION")] = "NYK"
    third[HEADERS.index("TEAM_NAME")] = "New York Knicks"
    third[HEADERS.index("MATCHUP")] = "NYK @ BOS"
    three_team_rows.append(third)

    for rows in (one_team_rows, three_team_rows):
        with pytest.raises(ValueError, match="team identities, expected 2"):
            player_logs.normalize_payload(_payload(rows), season=1984, game_type="regular")


def test_normalize_payload_rejects_two_source_teams_resolving_to_one_franchise():
    rows = [list(row) for row in REGULAR_ROWS]
    for row in rows:
        if row[HEADERS.index("TEAM_ABBREVIATION")] == "CHI":
            row[HEADERS.index("TEAM_NAME")] = "Boston Celtics"
    with pytest.raises(ValueError, match="same resolved franchise"):
        player_logs.normalize_payload(_payload(rows), season=1984, game_type="regular")


def test_normalize_payload_rejects_duplicate_game_and_person_key():
    rows = [list(row) for row in REGULAR_ROWS]
    rows.append(list(rows[0]))
    with pytest.raises(ValueError, match=r"duplicate \(game_id, person_id\)"):
        player_logs.normalize_payload(_payload(rows), season=1984, game_type="regular")


def test_normalize_payload_assigns_neutral_style_rows_by_each_players_position():
    rows = [list(row) for row in REGULAR_ROWS]
    for row in rows:
        row[HEADERS.index("MATCHUP")] = "CHI @ BOS"

    frame = player_logs.normalize_payload(_payload(rows), season=1984, game_type="regular")
    assert set(frame.loc[frame.is_home, "franchise_id"]) == {"BOS"}
    assert set(frame.loc[~frame.is_home, "franchise_id"]) == {"CHI"}


def test_normalize_payload_rejects_identifier_outside_signed_int64_range():
    rows = _rows_with("PLAYER_ID", 1e20)
    with pytest.raises(ValueError, match="PLAYER_ID must be an exact integer"):
        player_logs.normalize_payload(_payload(rows), season=1984, game_type="regular")


def test_normalize_payload_rejects_non_standard_json_numeric_constants():
    rows = _rows_with("PTS", float("nan"))
    with pytest.raises(ValueError, match="invalid player-log JSON"):
        player_logs.normalize_payload(_payload(rows), season=1984, game_type="regular")


def test_normalize_payload_rejects_team_rows_that_disagree_on_matchup_pair():
    rows = [list(row) for row in REGULAR_ROWS]
    rows[3][HEADERS.index("MATCHUP")] = "BOS vs. NYK"
    with pytest.raises(ValueError, match="rows disagree on MATCHUP team pair"):
        player_logs.normalize_payload(_payload(rows), season=1984, game_type="regular")


def test_normalize_payload_requires_exactly_one_home_team_identity():
    rows = [list(row) for row in REGULAR_ROWS]
    for row in rows:
        if row[HEADERS.index("TEAM_ABBREVIATION")] == "CHI":
            row[HEADERS.index("MATCHUP")] = "CHI vs. BOS"
    with pytest.raises(ValueError, match="has 2 home team identities, expected 1"):
        player_logs.normalize_payload(_payload(rows), season=1984, game_type="regular")
