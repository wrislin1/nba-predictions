"""Tests for exact play-by-play worklist normalization and cross-validation."""

from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path

import pandas as pd
import pytest

from nba_data import cache, pbp, pbp_build


FIXTURES = Path(__file__).resolve().parent / "fixtures/pbp"

WORKLIST_HEADERS = [
    "SEASON_ID",
    "TEAM_ID",
    "TEAM_NAME",
    "TEAM_ABBREVIATION",
    "GAME_ID",
    "GAME_DATE",
    "MATCHUP",
    "WL",
    "PTS",
]

REGULAR_1997_ROWS = [
    ["21996", 1610612741, "Chicago Bulls", "CHI", "0029600001",
     "1996-11-01", "CHI @ BOS", "W", 107],
    ["21996", 1610612738, "Boston Celtics", "BOS", "0029600001",
     "1996-11-01", "BOS vs. CHI", "L", 98],
]
PLAYOFF_1997_ROWS = [
    ["41996", 1610612766, "Charlotte Hornets", "CHH", "0049600001",
     "1997-04-24", "CHH @ NYK", "L", 99],
    ["41996", 1610612752, "New York Knicks", "NYK", "0049600001",
     "1997-04-24", "NYK vs. CHH", "W", 109],
]
PLAY_IN_2020_ROWS = [
    ["52019", 1610612757, "Portland Trail Blazers", "POR", "0051900111",
     "2020-08-15", "POR vs. MEM", "W", 126],
    ["52019", 1610612763, "Memphis Grizzlies", "MEM", "0051900111",
     "2020-08-15", "MEM @ POR", "L", 122],
]


# ---------------------------------------------------------------------------
# Step 2: normalization and identity pairing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture", "season", "game_type", "expected"),
    [
        (
            "worklist_regular_1997.json",
            1997,
            "regular",
            ["0029600001", "CHI", "BOS", 107, 98],
        ),
        (
            "worklist_playoff_1997.json",
            1997,
            "playoff",
            ["0049600001", "NOP", "NYK", 99, 109],
        ),
        (
            "worklist_play_in_2020.json",
            2020,
            "play_in",
            ["0051900111", "MEM", "POR", 122, 126],
        ),
    ],
)
def test_normalize_worklist_pairs_home_and_away(
    fixture, season, game_type, expected
):
    frame = pbp.normalize_worklist_payload(
        (FIXTURES / fixture).read_bytes(),
        season=season,
        game_type=game_type,
    )
    row = frame.iloc[0]
    assert [
        row["game_id"],
        row["away_franchise_id"],
        row["home_franchise_id"],
        row["away_pts"],
        row["home_pts"],
    ] == expected
    assert frame.columns.tolist() == pbp.WORKLIST_COLUMNS


def test_normalize_worklist_orders_rows_and_types_game_date():
    frame = pbp.normalize_worklist_payload(
        (FIXTURES / "worklist_regular_1997.json").read_bytes(),
        season=1997,
        game_type="regular",
    )
    assert frame["game_date"].iloc[0] == date(1996, 11, 1)
    assert frame["season"].iloc[0] == 1997
    assert frame["game_type"].iloc[0] == "regular"
    assert list(frame.columns) == [
        "game_id", "season", "game_type", "game_date",
        "away_franchise_id", "home_franchise_id", "away_pts", "home_pts",
    ]


def test_normalize_worklist_empty_row_set_returns_empty_frame():
    document = {
        "resultSets": [
            {
                "name": "LeagueGameFinderResults",
                "headers": WORKLIST_HEADERS,
                "rowSet": [],
            }
        ]
    }
    frame = pbp.normalize_worklist_payload(
        json.dumps(document).encode("utf-8"), season=1997, game_type="regular"
    )
    assert frame.empty
    assert frame.columns.tolist() == pbp.WORKLIST_COLUMNS


# ---------------------------------------------------------------------------
# Step 2 (continued): failing/invalid payload variations
# ---------------------------------------------------------------------------


def _document(rows=None, headers=None):
    return {
        "resultSets": [
            {
                "name": "LeagueGameFinderResults",
                "headers": list(headers or WORKLIST_HEADERS),
                "rowSet": [list(row) for row in (rows or REGULAR_1997_ROWS)],
            }
        ]
    }


def _payload(document):
    return json.dumps(document).encode("utf-8")


def _identity(document):
    pass


def _wrong_result_set_name(document):
    document["resultSets"][0]["name"] = "LeagueGameFinderParameterValues"


def _missing_header(document):
    document["resultSets"][0]["headers"].remove("PTS")


def _malformed_row_width(document):
    document["resultSets"][0]["rowSet"][0].pop()


def _one_team_row(document):
    document["resultSets"][0]["rowSet"].pop()


def _three_team_rows(document):
    row_set = document["resultSets"][0]["rowSet"]
    row_set.append(list(row_set[1]))


def _two_home_rows(document):
    document["resultSets"][0]["rowSet"][0][6] = "CHI vs. BOS"


def _no_home_row(document):
    document["resultSets"][0]["rowSet"][1][6] = "BOS @ CHI"


def _invalid_pts(document):
    document["resultSets"][0]["rowSet"][0][8] = -5


def _same_franchise_both_sides(document):
    row = document["resultSets"][0]["rowSet"][0]
    row[2] = "Boston Celtics"
    row[3] = "BOS"


@pytest.mark.parametrize(
    ("build_document", "mutator", "season", "game_type", "match"),
    [
        (
            _document, _wrong_result_set_name,
            1997, "regular", "LeagueGameFinderResults",
        ),
        (
            _document, _missing_header,
            1997, "regular", "missing required header",
        ),
        (
            _document, _malformed_row_width,
            1997, "regular", "row width",
        ),
        (
            _document, _one_team_row,
            1997, "regular", "team rows, expected 2",
        ),
        (
            _document, _three_team_rows,
            1997, "regular", "team rows, expected 2",
        ),
        (
            _document, _two_home_rows,
            1997, "regular", "home rows, expected 1",
        ),
        (
            _document, _no_home_row,
            1997, "regular", "home rows, expected 1",
        ),
        (
            _document, _invalid_pts,
            1997, "regular", "PTS must be non-negative",
        ),
        (
            _document, _same_franchise_both_sides,
            1997, "regular", "same franchise",
        ),
        (
            _document, _identity,
            1998, "regular", "ending season mismatch",
        ),
        (
            lambda: _document(rows=PLAYOFF_1997_ROWS), _identity,
            1997, "exhibition", "unknown game type",
        ),
    ],
    ids=[
        "wrong-result-set-name",
        "missing-required-header",
        "malformed-row-width",
        "one-team-row",
        "three-team-rows",
        "two-home-rows",
        "no-home-row",
        "invalid-pts",
        "same-franchise-both-sides",
        "wrong-ending-season",
        "unknown-game-type",
    ],
)
def test_normalize_worklist_payload_rejects_invalid_payloads(
    build_document, mutator, season, game_type, match
):
    document = build_document()
    mutator(document)
    with pytest.raises(pbp.PBPValidationError, match=match):
        pbp.normalize_worklist_payload(
            _payload(document), season=season, game_type=game_type
        )


def test_normalize_worklist_payload_rejects_wrong_game_id_prefix():
    # Isolate the GAME_ID/ID_PREFIXES check from the SEASON_ID leading-digit
    # check: SEASON_ID correctly says "playoff" (leading digit 4), but the
    # GAME_ID keeps the regular-season prefix ("002" instead of "004").
    document = _document(rows=PLAYOFF_1997_ROWS)
    for row in document["resultSets"][0]["rowSet"]:
        row[4] = "0029600001"
    with pytest.raises(pbp.PBPValidationError, match="GAME_ID prefix mismatch"):
        pbp.normalize_worklist_payload(
            _payload(document), season=1997, game_type="playoff"
        )


def test_normalize_worklist_payload_rejects_unknown_game_type_before_parsing():
    # game_type validation must reject before any JSON parsing is attempted.
    with pytest.raises(pbp.PBPValidationError, match="unknown game type"):
        pbp.normalize_worklist_payload(
            b"not valid json", season=1997, game_type="exhibition"
        )


# ---------------------------------------------------------------------------
# Steps 5 & 7: cross-source equality against games.parquet /
# playoff_games.parquet
# ---------------------------------------------------------------------------

GAMES_COLUMNS = [
    "game_id", "season", "game_date",
    "away_franchise_id", "home_franchise_id", "away_pts", "home_pts",
]
PLAYOFF_COLUMNS = [
    "game_id", "season", "game_date", "game_type",
    "away_franchise_id", "home_franchise_id", "away_pts", "home_pts",
]

# Franchise names below all resolve to a stable, identical abbreviation
# across 1997-2026 (verified against src/nba_data/reference/franchises.csv),
# so synthetic multi-season fixtures do not need to reason about lineage
# aliasing (e.g. Charlotte Hornets -> NOP) outside the two dedicated exact
# fixtures already covered above.
STABLE_TEAMS = [
    ("Boston Celtics", "BOS"),
    ("Los Angeles Lakers", "LAL"),
    ("Golden State Warriors", "GSW"),
    ("Miami Heat", "MIA"),
    ("Milwaukee Bucks", "MIL"),
    ("Phoenix Suns", "PHX"),
    ("Portland Trail Blazers", "POR"),
    ("Denver Nuggets", "DEN"),
]


def _worklist_document(rows):
    return {
        "resultSets": [
            {
                "name": "LeagueGameFinderResults",
                "headers": WORKLIST_HEADERS,
                "rowSet": rows,
            }
        ]
    }


def _team_rows(season_id, game_id, game_date, away, home, away_pts, home_pts):
    away_name, away_abbr = away
    home_name, home_abbr = home
    return [
        [
            season_id, 1, away_name, away_abbr, game_id, game_date,
            f"{away_abbr} @ {home_abbr}", "L", away_pts,
        ],
        [
            season_id, 2, home_name, home_abbr, game_id, game_date,
            f"{home_abbr} vs. {away_abbr}", "W", home_pts,
        ],
    ]


def _write_worklist(data_dir, season, game_type, rows):
    path = pbp.worklist_path(data_dir, season, game_type)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(_worklist_document(rows)).encode("utf-8"))


def _regular_publication_row(game_id, season, game_date, away, home, away_pts, home_pts):
    return {
        "game_id": game_id,
        "season": season,
        "game_date": date.fromisoformat(game_date),
        "away_franchise_id": away[1],
        "home_franchise_id": home[1],
        "away_pts": away_pts,
        "home_pts": home_pts,
    }


def _postseason_publication_row(
    game_id, season, game_date, game_type, away, home, away_pts, home_pts
):
    return {
        "game_id": game_id,
        "season": season,
        "game_date": date.fromisoformat(game_date),
        "game_type": game_type,
        "away_franchise_id": away[1],
        "home_franchise_id": home[1],
        "away_pts": away_pts,
        "home_pts": home_pts,
    }


def seed_cross_source_publications(tmp_path):
    """Seed temporary regular/postseason Parquet publications plus their
    matching raw worklist files, then load the combined worklist.

    Covers seasons 1997 (regular + playoff) and 2020 (regular + playoff +
    play-in), matching the exact fixture games used elsewhere in this
    module so the two dedicated fixtures stay meaningful cross-checks.
    """
    data_dir = tmp_path / "data"

    _write_worklist(data_dir, 1997, "regular", list(REGULAR_1997_ROWS))
    _write_worklist(data_dir, 1997, "playoff", list(PLAYOFF_1997_ROWS))
    _write_worklist(data_dir, 2020, "playoff", [])
    _write_worklist(data_dir, 2020, "play_in", list(PLAY_IN_2020_ROWS))
    _write_worklist(
        data_dir, 2020, "regular",
        _team_rows(
            "22019", "0022000001", "2020-08-14",
            ("Denver Nuggets", "DEN"), ("Phoenix Suns", "PHX"), 100, 110,
        ),
    )

    games = pd.DataFrame(
        [
            _regular_publication_row(
                "0029600001", 1997, "1996-11-01",
                ("Chicago Bulls", "CHI"), ("Boston Celtics", "BOS"),
                107, 98,
            ),
            _regular_publication_row(
                "0022000001", 2020, "2020-08-14",
                ("Denver Nuggets", "DEN"), ("Phoenix Suns", "PHX"),
                100, 110,
            ),
        ],
        columns=GAMES_COLUMNS,
    )
    playoff_games = pd.DataFrame(
        [
            _postseason_publication_row(
                "0049600001", 1997, "1997-04-24", "playoff",
                ("Charlotte Hornets", "NOP"), ("New York Knicks", "NYK"),
                99, 109,
            ),
            _postseason_publication_row(
                "0051900111", 2020, "2020-08-15", "play_in",
                ("Memphis Grizzlies", "MEM"),
                ("Portland Trail Blazers", "POR"), 122, 126,
            ),
        ],
        columns=PLAYOFF_COLUMNS,
    )

    parquet_dir = data_dir / "parquet"
    parquet_dir.mkdir(parents=True, exist_ok=True)
    games.to_parquet(parquet_dir / "games.parquet", index=False)
    playoff_games.to_parquet(parquet_dir / "playoff_games.parquet", index=False)

    worklist = pbp.load_worklist(data_dir, [1997, 2020], require_complete=True)
    return data_dir, worklist


def mutate_publication(data_dir, publication, column, replacement):
    """Mutate one column of the first data row of a seeded publication."""
    name = "games.parquet" if publication == "regular" else "playoff_games.parquet"
    path = data_dir / "parquet" / name
    frame = pd.read_parquet(path)
    frame.loc[0, column] = replacement
    frame.to_parquet(path, index=False)


def test_cross_validate_worklist_accepts_exact_regular_and_postseason(tmp_path):
    data_dir, worklist = seed_cross_source_publications(tmp_path)
    pbp.cross_validate_worklist(worklist, data_dir, [1997, 2020])


@pytest.mark.parametrize(
    ("publication", "column", "replacement", "message"),
    [
        ("regular", "home_pts", 97, "regular worklist mismatch"),
        ("regular", "game_id", "0029600999", "regular worklist mismatch"),
        ("postseason", "away_pts", 98, "postseason worklist mismatch"),
        ("postseason", "game_type", "play_in", "postseason worklist mismatch"),
    ],
)
def test_cross_validate_worklist_rejects_bidirectional_mismatch(
    tmp_path, publication, column, replacement, message
):
    data_dir, worklist = seed_cross_source_publications(tmp_path)
    mutate_publication(data_dir, publication, column, replacement)
    with pytest.raises(pbp.PBPValidationError, match=message):
        pbp.cross_validate_worklist(worklist, data_dir, [1997, 2020])


@pytest.mark.parametrize(
    ("publication", "message"),
    [
        ("regular", "regular worklist mismatch"),
        ("postseason", "postseason worklist mismatch"),
    ],
)
def test_cross_validate_worklist_rejects_extra_publication_rows(
    tmp_path, publication, message
):
    # The extra row must use a comparison key not already present so it
    # cannot collide with an existing publication row (the postseason key
    # omits game_id, so reusing an existing key would instead trip the
    # duplicate-key guard rather than the mismatch this test targets).
    data_dir, worklist = seed_cross_source_publications(tmp_path)
    if publication == "regular":
        name = "games.parquet"
        columns = GAMES_COLUMNS
        extra = _regular_publication_row(
            "0029600002", 1997, "1996-11-02",
            ("Los Angeles Lakers", "LAL"), ("Miami Heat", "MIA"), 100, 101,
        )
    else:
        name = "playoff_games.parquet"
        columns = PLAYOFF_COLUMNS
        extra = _postseason_publication_row(
            "0049600002", 1997, "1997-04-25", "playoff",
            ("Los Angeles Lakers", "LAL"), ("Miami Heat", "MIA"), 100, 101,
        )
    path = data_dir / "parquet" / name
    frame = pd.read_parquet(path)
    frame = pd.concat(
        [frame, pd.DataFrame([extra], columns=columns)], ignore_index=True
    )
    frame.to_parquet(path, index=False)
    with pytest.raises(pbp.PBPValidationError, match=message):
        pbp.cross_validate_worklist(worklist, data_dir, [1997, 2020])


def test_cross_validate_worklist_excludes_tiebreaker_and_voided_rows(tmp_path):
    data_dir, worklist = seed_cross_source_publications(tmp_path)
    path = data_dir / "parquet" / "playoff_games.parquet"
    frame = pd.read_parquet(path)
    extras = pd.DataFrame(
        [
            _postseason_publication_row(
                "bref-199700000TIE", 1997, "1997-04-25", "tiebreaker",
                ("Boston Celtics", "BOS"), ("Los Angeles Lakers", "LAL"),
                90, 91,
            ),
            _postseason_publication_row(
                "bref-199700000VOI", 1997, "1997-04-26", "voided",
                ("Boston Celtics", "BOS"), ("Los Angeles Lakers", "LAL"),
                0, 0,
            ),
        ],
        columns=PLAYOFF_COLUMNS,
    )
    frame = pd.concat([frame, extras], ignore_index=True)
    frame.to_parquet(path, index=False)
    # tiebreaker/voided rows are excluded from the expected PBP set, so
    # their presence in the publication must not raise.
    pbp.cross_validate_worklist(worklist, data_dir, [1997, 2020])


def test_cross_validate_worklist_rejects_duplicate_worklist_game_ids(tmp_path):
    data_dir, worklist = seed_cross_source_publications(tmp_path)
    duplicated = pd.concat(
        [worklist, worklist.iloc[[0]]], ignore_index=True
    )
    with pytest.raises(pbp.PBPValidationError, match="duplicate"):
        pbp.cross_validate_worklist(duplicated, data_dir, [1997, 2020])


def test_cross_validate_worklist_play_in_counts_are_exact(tmp_path):
    data_dir = tmp_path / "data"
    parquet_dir = data_dir / "parquet"
    parquet_dir.mkdir(parents=True, exist_ok=True)

    expected_play_in_counts = {
        2020: 1, 2021: 6, 2022: 6, 2023: 6, 2024: 6, 2025: 6, 2026: 6,
    }
    seasons = list(expected_play_in_counts)

    games_rows = []
    playoff_rows = []
    for season in seasons:
        starting_year = season - 1
        # One regular-season game per season, so the regular comparison
        # also stays exact for every scoped season.
        away, home = STABLE_TEAMS[0], STABLE_TEAMS[1]
        regular_game_id = f"002{starting_year % 100:02d}00001"
        _write_worklist(
            data_dir, season, "regular",
            _team_rows(
                f"2{starting_year}", regular_game_id, f"{starting_year}-11-01",
                away, home, 100, 101,
            ),
        )
        games_rows.append(_regular_publication_row(
            regular_game_id, season, f"{starting_year}-11-01", away, home,
            100, 101,
        ))

        # One playoff game per season.
        away, home = STABLE_TEAMS[2], STABLE_TEAMS[3]
        playoff_game_id = f"004{starting_year % 100:02d}00001"
        _write_worklist(
            data_dir, season, "playoff",
            _team_rows(
                f"4{starting_year}", playoff_game_id, f"{season}-04-20",
                away, home, 90, 95,
            ),
        )
        playoff_rows.append(_postseason_publication_row(
            playoff_game_id, season, f"{season}-04-20", "playoff",
            away, home, 90, 95,
        ))

        # Exact play-in games for the season.
        play_in_rows = []
        for index in range(expected_play_in_counts[season]):
            away = STABLE_TEAMS[(4 + index) % len(STABLE_TEAMS)]
            home = STABLE_TEAMS[(5 + index) % len(STABLE_TEAMS)]
            play_in_game_id = f"005{starting_year % 100:02d}{index:05d}"
            play_in_rows.extend(_team_rows(
                f"5{starting_year}", play_in_game_id, f"{season}-04-18",
                away, home, 111, 112,
            ))
            playoff_rows.append(_postseason_publication_row(
                play_in_game_id, season, f"{season}-04-18", "play_in",
                away, home, 111, 112,
            ))
        _write_worklist(data_dir, season, "play_in", play_in_rows)

    pd.DataFrame(games_rows, columns=GAMES_COLUMNS).to_parquet(
        parquet_dir / "games.parquet", index=False
    )
    pd.DataFrame(playoff_rows, columns=PLAYOFF_COLUMNS).to_parquet(
        parquet_dir / "playoff_games.parquet", index=False
    )

    worklist = pbp.load_worklist(data_dir, seasons, require_complete=True)
    counts = {
        season: int(
            (worklist["season"].eq(season) & worklist["game_type"].eq("play_in")).sum()
        )
        for season in seasons
    }
    assert counts == expected_play_in_counts

    pbp.cross_validate_worklist(worklist, data_dir, seasons)


# ---------------------------------------------------------------------------
# load_worklist completeness behavior
# ---------------------------------------------------------------------------


def test_load_worklist_require_complete_true_raises_on_missing_files(tmp_path):
    data_dir = tmp_path / "data"
    _write_worklist(data_dir, 1997, "regular", list(REGULAR_1997_ROWS))
    # playoff.json intentionally left unwritten for season 1997.
    with pytest.raises(pbp.PBPValidationError, match="missing required worklist"):
        pbp.load_worklist(data_dir, [1997], require_complete=True)


def test_load_worklist_require_complete_false_skips_missing_files(tmp_path):
    data_dir = tmp_path / "data"
    _write_worklist(data_dir, 1997, "regular", list(REGULAR_1997_ROWS))
    frame = pbp.load_worklist(data_dir, [1997], require_complete=False)
    assert frame["game_type"].tolist() == ["regular"]
    assert frame.columns.tolist() == pbp.WORKLIST_COLUMNS


# ---------------------------------------------------------------------------
# Task 3: exact cache identities, transport validators, and complete
# recursive inventories
# ---------------------------------------------------------------------------


def expected_game_metadata(season, game_type, game_id):
    return {
        "provider": "stats_nba",
        "resource": "play_by_play",
        "endpoint": "PlayByPlayV3",
        "parameters": {
            "GameID": game_id,
            "StartPeriod": 0,
            "EndPeriod": 0,
        },
        "season": season,
        "season_label": pbp.season_label(season),
        "game_type": game_type,
        "game_id": game_id,
    }


def expected_worklist_metadata(season, game_type):
    return {
        "provider": "stats_nba",
        "resource": "pbp_worklist",
        "endpoint": "LeagueGameFinder",
        "parameters": {
            "Season": pbp.season_label(season),
            "SeasonType": pbp.SEASON_TYPES[game_type],
        },
        "season": season,
        "season_label": pbp.season_label(season),
        "game_type": game_type,
    }


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


def _minimal_game_payload(game_id):
    return json.dumps(
        {"game": {"gameId": game_id, "actions": [{"actionId": 1}]}}
    ).encode("utf-8")


def _raw_root(data_dir):
    return data_dir / "raw/stats_nba/pbp"


# ---------------------------------------------------------------------------
# Step 1: exact metadata identity at publish time
# ---------------------------------------------------------------------------


WORKLIST_VALUE_MUTATIONS = {
    "provider": "other_provider",
    "resource": "other_resource",
    "endpoint": "OtherEndpoint",
    "season": 1998,
    "season_label": "wrong-label",
    "game_type": "playoff",
}
WORKLIST_TYPE_MUTATIONS = {
    "provider": 1,
    "resource": 1,
    "endpoint": 1,
    "season": "1997",
    "season_label": 199798,
    "game_type": 1,
}


@pytest.mark.parametrize("field", sorted(WORKLIST_VALUE_MUTATIONS))
def test_publish_worklist_payload_rejects_metadata_value_mismatch(tmp_path, field):
    metadata = expected_worklist_metadata(1997, "regular")
    metadata[field] = WORKLIST_VALUE_MUTATIONS[field]
    destination = pbp.worklist_path(tmp_path, 1997, "regular")
    payload = json.dumps(_document(rows=REGULAR_1997_ROWS)).encode("utf-8")
    with pytest.raises(
        cache.CacheIntegrityError, match="PBP cache metadata mismatch"
    ):
        pbp.publish_worklist_payload(destination, payload, metadata)
    assert not destination.exists()


@pytest.mark.parametrize("field", sorted(WORKLIST_TYPE_MUTATIONS))
def test_publish_worklist_payload_rejects_metadata_type_mismatch(tmp_path, field):
    metadata = expected_worklist_metadata(1997, "regular")
    metadata[field] = WORKLIST_TYPE_MUTATIONS[field]
    destination = pbp.worklist_path(tmp_path, 1997, "regular")
    payload = json.dumps(_document(rows=REGULAR_1997_ROWS)).encode("utf-8")
    with pytest.raises(
        cache.CacheIntegrityError, match="PBP cache metadata mismatch"
    ):
        pbp.publish_worklist_payload(destination, payload, metadata)


def test_publish_worklist_payload_accepts_exact_metadata(tmp_path):
    metadata = expected_worklist_metadata(1997, "regular")
    destination = pbp.worklist_path(tmp_path, 1997, "regular")
    payload = json.dumps(_document(rows=REGULAR_1997_ROWS)).encode("utf-8")
    result = pbp.publish_worklist_payload(destination, payload, metadata)
    assert result == destination
    assert destination.read_bytes() == payload


GAME_VALUE_MUTATIONS = {
    "provider": "other_provider",
    "resource": "other_resource",
    "endpoint": "OtherEndpoint",
    "season": 1998,
    "season_label": "wrong-label",
    "game_id": "0000000000",
}
GAME_TYPE_MUTATIONS = {
    "provider": 1,
    "resource": 1,
    "endpoint": 1,
    "season": "1997",
    "season_label": 199798,
    "game_id": 29600001,
}


@pytest.mark.parametrize("field", sorted(GAME_VALUE_MUTATIONS))
def test_publish_game_payload_rejects_metadata_value_mismatch(tmp_path, field):
    metadata = expected_game_metadata(1997, "regular", "0029600001")
    metadata[field] = GAME_VALUE_MUTATIONS[field]
    destination = pbp.game_path(tmp_path, 1997, "0029600001")
    payload = _minimal_game_payload("0029600001")
    with pytest.raises(
        cache.CacheIntegrityError, match="PBP cache metadata mismatch"
    ):
        pbp.publish_game_payload(destination, payload, metadata)
    assert not destination.exists()


@pytest.mark.parametrize("field", sorted(GAME_TYPE_MUTATIONS))
def test_publish_game_payload_rejects_metadata_type_mismatch(tmp_path, field):
    metadata = expected_game_metadata(1997, "regular", "0029600001")
    metadata[field] = GAME_TYPE_MUTATIONS[field]
    destination = pbp.game_path(tmp_path, 1997, "0029600001")
    payload = _minimal_game_payload("0029600001")
    with pytest.raises(
        cache.CacheIntegrityError, match="PBP cache metadata mismatch"
    ):
        pbp.publish_game_payload(destination, payload, metadata)


@pytest.mark.parametrize(
    ("field", "value"),
    [("GameID", "0000000000"), ("StartPeriod", 1), ("EndPeriod", 1)],
)
def test_publish_game_payload_rejects_parameters_value_mismatch(
    tmp_path, field, value
):
    metadata = expected_game_metadata(1997, "regular", "0029600001")
    metadata["parameters"] = {**metadata["parameters"], field: value}
    destination = pbp.game_path(tmp_path, 1997, "0029600001")
    payload = _minimal_game_payload("0029600001")
    with pytest.raises(
        cache.CacheIntegrityError, match="PBP cache metadata mismatch"
    ):
        pbp.publish_game_payload(destination, payload, metadata)


@pytest.mark.parametrize(
    ("field", "value"),
    [("GameID", 29600001), ("StartPeriod", "0"), ("EndPeriod", False)],
)
def test_publish_game_payload_rejects_parameters_type_mismatch(
    tmp_path, field, value
):
    metadata = expected_game_metadata(1997, "regular", "0029600001")
    metadata["parameters"] = {**metadata["parameters"], field: value}
    destination = pbp.game_path(tmp_path, 1997, "0029600001")
    payload = _minimal_game_payload("0029600001")
    with pytest.raises(
        cache.CacheIntegrityError, match="PBP cache metadata mismatch"
    ):
        pbp.publish_game_payload(destination, payload, metadata)


def test_publish_game_payload_accepts_exact_metadata(tmp_path):
    metadata = expected_game_metadata(1997, "regular", "0029600001")
    destination = pbp.game_path(tmp_path, 1997, "0029600001")
    payload = _minimal_game_payload("0029600001")
    result = pbp.publish_game_payload(destination, payload, metadata)
    assert result == destination
    assert destination.read_bytes() == payload


# ---------------------------------------------------------------------------
# Step 2: transport validation vs. fixed V3 schema separation
# ---------------------------------------------------------------------------


def test_transport_valid_schema_drift_remains_cached(tmp_path):
    destination = pbp.game_path(tmp_path, 1997, "0029600001")
    payload = json.dumps({
        "game": {
            "gameId": "0029600001",
            "actions": [{"newUpstreamField": 1}],
        }
    }).encode("utf-8")
    pbp.publish_game_payload(
        destination,
        payload,
        expected_game_metadata(1997, "regular", "0029600001"),
    )
    assert destination.read_bytes() == payload
    with pytest.raises(
        pbp_build.PBPBuildError,
        match="V3 action schema drift",
    ):
        pbp_build.parse_game_payload(payload, worklist_row())


def test_publish_game_payload_rejects_malformed_json_transport(tmp_path):
    destination = pbp.game_path(tmp_path, 1997, "0029600001")
    with pytest.raises(json.JSONDecodeError):
        pbp.publish_game_payload(
            destination,
            b"not json",
            expected_game_metadata(1997, "regular", "0029600001"),
        )
    assert not destination.exists()


@pytest.mark.parametrize(
    ("document", "match"),
    [
        ({}, "wrong outer game ID"),
        ({"game": "not-an-object"}, "wrong outer game ID"),
        (
            {"game": {"gameId": "0000000000", "actions": [{}]}},
            "wrong outer game ID",
        ),
        ({"game": {"gameId": "0029600001"}}, "invalid action envelope"),
        (
            {"game": {"gameId": "0029600001", "actions": "nope"}},
            "invalid action envelope",
        ),
        (
            {"game": {"gameId": "0029600001", "actions": []}},
            "invalid action envelope",
        ),
        (
            {"game": {"gameId": "0029600001", "actions": [1, 2]}},
            "invalid action envelope",
        ),
    ],
    ids=[
        "missing-game",
        "non-object-game",
        "wrong-game-id",
        "missing-actions",
        "non-list-actions",
        "empty-actions",
        "non-object-action-elements",
    ],
)
def test_validate_game_transport_rejects_malformed_envelopes(
    tmp_path, document, match
):
    path = tmp_path / "game.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(pbp.PBPValidationError, match=match):
        pbp.validate_game_transport(path, game_id="0029600001")


def test_validate_worklist_transport_accepts_valid_payload(tmp_path):
    path = tmp_path / "worklist.json"
    path.write_text(json.dumps(_document(rows=REGULAR_1997_ROWS)), encoding="utf-8")
    pbp.validate_worklist_transport(path, season=1997, game_type="regular")


def test_validate_worklist_transport_rejects_invalid_payload(tmp_path):
    path = tmp_path / "worklist.json"
    path.write_text(json.dumps(_document(rows=REGULAR_1997_ROWS)), encoding="utf-8")
    with pytest.raises(pbp.PBPValidationError, match="unknown game type"):
        pbp.validate_worklist_transport(path, season=1997, game_type="exhibition")


# ---------------------------------------------------------------------------
# Step 3: safe recursive cache-tree inventory validation
# ---------------------------------------------------------------------------


def _seed_valid_cache_tree(data_dir):
    """Publish two valid worklists and their two matching game pairs."""
    pbp.publish_worklist_payload(
        pbp.worklist_path(data_dir, 1997, "regular"),
        json.dumps(_document(rows=REGULAR_1997_ROWS)).encode("utf-8"),
        expected_worklist_metadata(1997, "regular"),
    )
    pbp.publish_worklist_payload(
        pbp.worklist_path(data_dir, 1997, "playoff"),
        json.dumps(_document(rows=PLAYOFF_1997_ROWS)).encode("utf-8"),
        expected_worklist_metadata(1997, "playoff"),
    )
    pbp.publish_game_payload(
        pbp.game_path(data_dir, 1997, "0029600001"),
        _minimal_game_payload("0029600001"),
        expected_game_metadata(1997, "regular", "0029600001"),
    )
    pbp.publish_game_payload(
        pbp.game_path(data_dir, 1997, "0049600001"),
        _minimal_game_payload("0049600001"),
        expected_game_metadata(1997, "playoff", "0049600001"),
    )


def test_validate_cache_tree_accepts_valid_subset(tmp_path):
    data_dir = tmp_path / "data"
    _seed_valid_cache_tree(data_dir)
    inventory = pbp.validate_cache_tree(
        _raw_root(data_dir), required_seasons=pbp.SEASONS, require_complete=False
    )
    assert [p.name for p in inventory.worklist_bodies] == [
        "playoff.json", "regular.json",
    ]
    assert [p.name for p in inventory.game_bodies] == [
        "0029600001.json", "0049600001.json",
    ]
    assert inventory.worklist["game_id"].tolist() == ["0029600001", "0049600001"]
    assert inventory.worklist.columns.tolist() == pbp.WORKLIST_COLUMNS


def test_validate_cache_inventory_matches_validate_cache_tree(tmp_path):
    data_dir = tmp_path / "data"
    _seed_valid_cache_tree(data_dir)
    from_tree = pbp.validate_cache_tree(
        _raw_root(data_dir), required_seasons=[1997], require_complete=False
    )
    from_inventory = pbp.validate_cache_inventory(
        data_dir, required_seasons=[1997], require_complete=False
    )
    assert from_tree.worklist_bodies == from_inventory.worklist_bodies
    assert from_tree.game_bodies == from_inventory.game_bodies
    pd.testing.assert_frame_equal(from_tree.worklist, from_inventory.worklist)


def _mutate_missing_body(raw_root):
    (raw_root / "games/1997/0029600001.json").unlink()


def _mutate_missing_sidecar(raw_root):
    (raw_root / "games/1997/0029600001.json.meta.json").unlink()


def _mutate_orphan_json(raw_root):
    (raw_root / "games/1997/orphan.json").write_text("{}", encoding="utf-8")


def _mutate_orphan_sidecar(raw_root):
    (raw_root / "games/1997/9999999999.json.meta.json").write_text(
        json.dumps({"sha256": "x"}), encoding="utf-8"
    )


def _mutate_stale_part(raw_root):
    (raw_root / "games/1997/0029600001.json.part").write_bytes(b"stale")


def _mutate_symlinked_file(raw_root):
    target = raw_root / "worklists/1997/regular.json"
    playoff = raw_root / "worklists/1997/playoff.json"
    target.unlink()
    target.symlink_to(playoff)


def _mutate_fifo(raw_root):
    body = raw_root / "worklists/1997/playoff.json"
    body.unlink()
    os.mkfifo(body)


def _mutate_unexpected_season_dir(raw_root):
    season_dir = raw_root / "worklists/2099"
    season_dir.mkdir(parents=True)
    (season_dir / "regular.json").write_text("{}", encoding="utf-8")


def _mutate_unexpected_game_id(raw_root):
    games_dir = raw_root / "games/1997"
    (games_dir / "0029699999.json").write_text("{}", encoding="utf-8")
    (games_dir / "0029699999.json.meta.json").write_text(
        json.dumps({"sha256": "x"}), encoding="utf-8"
    )


def _mutate_pair_absent_from_worklist(raw_root):
    games_dir = raw_root / "games/1997"
    (games_dir / "0021700123.json").write_text("{}", encoding="utf-8")
    (games_dir / "0021700123.json.meta.json").write_text(
        json.dumps({"sha256": "x"}), encoding="utf-8"
    )


def _mutate_wrong_game_under_season(raw_root):
    source_body = raw_root / "games/1997/0049600001.json"
    source_meta = raw_root / "games/1997/0049600001.json.meta.json"
    destination_dir = raw_root / "games/2020"
    destination_dir.mkdir(parents=True)
    (destination_dir / "0049600001.json").write_bytes(source_body.read_bytes())
    (destination_dir / "0049600001.json.meta.json").write_bytes(
        source_meta.read_bytes()
    )


@pytest.mark.parametrize(
    ("mutator", "exception", "match"),
    [
        (_mutate_missing_body, cache.CacheIntegrityError, "incomplete cache pair"),
        (_mutate_missing_sidecar, cache.CacheIntegrityError, "incomplete cache pair"),
        (_mutate_orphan_json, pbp.PBPValidationError, "unexpected cache entry"),
        (_mutate_orphan_sidecar, pbp.PBPValidationError, "unexpected cache entry"),
        (_mutate_stale_part, pbp.PBPValidationError, "unexpected cache entry"),
        (_mutate_symlinked_file, pbp.PBPValidationError, "unsafe entry"),
        (_mutate_fifo, pbp.PBPValidationError, "unsafe entry"),
        (
            _mutate_unexpected_season_dir,
            pbp.PBPValidationError,
            "unexpected cache entry",
        ),
        (_mutate_unexpected_game_id, pbp.PBPValidationError, "unexpected cache entry"),
        (
            _mutate_pair_absent_from_worklist,
            pbp.PBPValidationError,
            "unexpected cache entry",
        ),
        (
            _mutate_wrong_game_under_season,
            pbp.PBPValidationError,
            "unexpected cache entry",
        ),
    ],
    ids=[
        "missing-body",
        "missing-sidecar",
        "orphan-unrelated-json",
        "orphan-unrelated-sidecar",
        "stale-part",
        "symlinked-file",
        "fifo",
        "unexpected-season-directory",
        "unexpected-game-id",
        "pair-absent-from-worklist",
        "wrong-game-under-season-directory",
    ],
)
def test_validate_cache_tree_rejects_unsafe_and_unrelated_entries(
    tmp_path, mutator, exception, match
):
    data_dir = tmp_path / "data"
    _seed_valid_cache_tree(data_dir)
    raw_root = _raw_root(data_dir)
    mutator(raw_root)
    with pytest.raises(exception, match=match):
        pbp.validate_cache_tree(
            raw_root, required_seasons=pbp.SEASONS, require_complete=False
        )


def test_validate_cache_tree_rejects_game_type_metadata_mismatch_on_disk(tmp_path):
    # publish_game_payload cannot itself know a game's true game_type (that
    # requires the worklist row), so this defends against a game sidecar
    # whose game_type disagrees with the worklist that names it.
    data_dir = tmp_path / "data"
    _seed_valid_cache_tree(data_dir)
    raw_root = _raw_root(data_dir)
    meta_path = raw_root / "games/1997/0029600001.json.meta.json"
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    metadata["game_type"] = "playoff"
    meta_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(
        cache.CacheIntegrityError, match="PBP cache metadata mismatch"
    ):
        pbp.validate_cache_tree(
            raw_root, required_seasons=[1997], require_complete=False
        )


def test_validate_cache_tree_rejects_worklist_metadata_mismatch_on_disk(tmp_path):
    data_dir = tmp_path / "data"
    _seed_valid_cache_tree(data_dir)
    raw_root = _raw_root(data_dir)
    meta_path = raw_root / "worklists/1997/regular.json.meta.json"
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    metadata["provider"] = "corrupted"
    meta_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(
        cache.CacheIntegrityError, match="PBP cache metadata mismatch"
    ):
        pbp.validate_cache_tree(
            raw_root, required_seasons=pbp.SEASONS, require_complete=False
        )


def test_validate_cache_tree_require_complete_accepts_full_pair(tmp_path):
    data_dir = tmp_path / "data"
    _seed_valid_cache_tree(data_dir)
    inventory = pbp.validate_cache_tree(
        _raw_root(data_dir), required_seasons=[1997], require_complete=True
    )
    assert len(inventory.worklist_bodies) == 2
    assert len(inventory.game_bodies) == 2


def test_validate_cache_tree_require_complete_rejects_missing_game(tmp_path):
    data_dir = tmp_path / "data"
    _seed_valid_cache_tree(data_dir)
    raw_root = _raw_root(data_dir)
    (raw_root / "games/1997/0049600001.json").unlink()
    (raw_root / "games/1997/0049600001.json.meta.json").unlink()
    with pytest.raises(pbp.PBPValidationError, match="missing required game file"):
        pbp.validate_cache_tree(raw_root, required_seasons=[1997], require_complete=True)


def test_validate_cache_tree_require_complete_rejects_missing_worklist(tmp_path):
    data_dir = tmp_path / "data"
    _seed_valid_cache_tree(data_dir)
    raw_root = _raw_root(data_dir)
    (raw_root / "worklists/1997/playoff.json").unlink()
    (raw_root / "worklists/1997/playoff.json.meta.json").unlink()
    with pytest.raises(
        pbp.PBPValidationError, match="missing required worklist file"
    ):
        pbp.validate_cache_tree(raw_root, required_seasons=[1997], require_complete=True)


def test_validate_cache_tree_require_complete_true_requires_all_67_worklists(
    tmp_path,
):
    data_dir = tmp_path / "data"
    raw_root = _raw_root(data_dir)
    with pytest.raises(
        pbp.PBPValidationError, match="missing required worklist file"
    ) as excinfo:
        pbp.validate_cache_tree(
            raw_root, required_seasons=pbp.SEASONS, require_complete=True
        )
    expected_count = sum(
        2 + (1 if season in pbp.PLAY_IN_SEASONS else 0) for season in pbp.SEASONS
    )
    assert expected_count == 67
    assert str(excinfo.value).count(".json") == 67
