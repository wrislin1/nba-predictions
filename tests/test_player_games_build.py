import json
import hashlib
import importlib.metadata
import os
from pathlib import Path
import re
import subprocess
import sys

import pyarrow as pa
import pyarrow.parquet as pq
import pandas as pd
import pytest

from nba_data import (
    cache,
    franchises,
    player_games_build,
    player_logs,
    publication,
)


HEADERS = [
    "SEASON_ID", "PLAYER_ID", "PLAYER_NAME", "TEAM_ID",
    "TEAM_ABBREVIATION", "TEAM_NAME", "GAME_ID", "GAME_DATE",
    "MATCHUP", "WL", "MIN", "FGM", "FGA", "FG_PCT", "FG3M",
    "FG3A", "FG3_PCT", "FTM", "FTA", "FT_PCT", "OREB", "DREB",
    "REB", "AST", "STL", "BLK", "TOV", "PF", "PTS", "PLUS_MINUS",
]


def _source_row(
    *, season_id, game_id, game_date, player_id, team_id,
    abbreviation, team_name, matchup,
):
    return [
        season_id, player_id, f"Player {player_id}", team_id, abbreviation,
        team_name, game_id, game_date, matchup, "W", 24, 1, 2, 0.5, 0, 0,
        0.0, 0, 0, 0.0, 0, 1, 1, 1, 0, 0, 0, 1, 2, None,
    ]


def _payload(season, game_type):
    prefix = {"regular": "002", "playoff": "004"}[game_type]
    game_id = f"{prefix}{str(season - 1)[-2:]}00001"
    season_id = f"{prefix[-1]}{season - 1}"
    game_date = f"{season - 1}-10-28"
    rows = [
        _source_row(
            season_id=season_id, game_id=game_id, game_date=game_date,
            player_id=2, team_id=1610612741, abbreviation="CHI",
            team_name="Chicago Bulls", matchup="CHI @ BOS",
        ),
        _source_row(
            season_id=season_id, game_id=game_id, game_date=game_date,
            player_id=1, team_id=1610612738, abbreviation="BOS",
            team_name="Boston Celtics", matchup="BOS vs. CHI",
        ),
    ]
    return json.dumps({
        "resultSets": [{
            "name": "LeagueGameLog", "headers": HEADERS, "rowSet": rows,
        }]
    }).encode()


MATCHUP_FIXTURE_KEY = "1984|regular|0028300001|2"
MATCHUP_FIXTURE_ENTRY = {
    "season": 1984,
    "game_type": "regular",
    "game_id": "0028300001",
    "person_id": 2,
    "game_date": "1983-10-28",
    "nba_team_id": 1610612741,
    "team_name_raw": "Chicago Bulls",
    "team_abbreviation_raw": "CHI",
    "matchup_raw": "CHI @ CHI",
    "expected_home_abbreviation": "BOS",
    "expected_away_abbreviation": "CHI",
    "note": (
        "Reviewed source anomaly; same-game valid rows and committed references "
        "agree on the expected ordered matchup."
    ),
}
MATCHUP_FIXTURE_LEDGER_BYTES = (
    b'{\n'
    b'  "exceptions": {\n'
    b'    "1984|regular|0028300001|2": {\n'
    b'      "expected_away_abbreviation": "CHI",\n'
    b'      "expected_home_abbreviation": "BOS",\n'
    b'      "game_date": "1983-10-28",\n'
    b'      "game_id": "0028300001",\n'
    b'      "game_type": "regular",\n'
    b'      "matchup_raw": "CHI @ CHI",\n'
    b'      "nba_team_id": 1610612741,\n'
    b'      "note": "Reviewed source anomaly; same-game valid rows and committed references agree on the expected ordered matchup.",\n'
    b'      "person_id": 2,\n'
    b'      "season": 1984,\n'
    b'      "team_abbreviation_raw": "CHI",\n'
    b'      "team_name_raw": "Chicago Bulls"\n'
    b'    }\n'
    b'  },\n'
    b'  "schema_version": 1\n'
    b'}\n'
)

DESIGNATION_FIXTURE_KEY = "1984|regular|0028300001"
DESIGNATION_FIXTURE_NOTE = (
    "Reviewed neutral-site designation anomaly; exact reciprocal player-log "
    "shape and unique hoopR-backed committed reference agree on the "
    "designated sides."
)
DESIGNATION_FIXTURE_ENTRY = {
    "season": 1984,
    "game_type": "regular",
    "game_id": "0028300001",
    "game_date": "1983-10-28",
    "teams": [
        {
            "nba_team_id": 1610612738,
            "team_name_raw": "Boston Celtics",
            "team_abbreviation_raw": "BOS",
            "matchup_raw": "BOS @ CHI",
            "row_count": 1,
        },
        {
            "nba_team_id": 1610612741,
            "team_name_raw": "Chicago Bulls",
            "team_abbreviation_raw": "CHI",
            "matchup_raw": "CHI @ BOS",
            "row_count": 1,
        },
    ],
    "expected_home_abbreviation": "BOS",
    "expected_away_abbreviation": "CHI",
    "expected_home_franchise_id": "BOS",
    "expected_away_franchise_id": "CHI",
    "reference_neutral_site": True,
    "reference_neutral_site_source": "hoopr",
    "note": DESIGNATION_FIXTURE_NOTE,
}
DESIGNATION_FIXTURE_LEDGER_BYTES = (
    json.dumps(
        {
            "schema_version": 1,
            "designations": {
                DESIGNATION_FIXTURE_KEY: DESIGNATION_FIXTURE_ENTRY,
            },
        },
        sort_keys=True,
        indent=2,
    )
    + "\n"
).encode("utf-8")
EMPTY_DESIGNATION_LEDGER_BYTES = b'{"designations":{},"schema_version":1}\n'


def _matchup_payload(season, game_type):
    document = json.loads(_payload(season, game_type))
    result_set = document["resultSets"][0]
    rows = result_set["rowSet"]
    malformed = rows[0]
    malformed[HEADERS.index("MATCHUP")] = "CHI @ CHI"
    valid_same_team = list(malformed)
    valid_same_team[HEADERS.index("PLAYER_ID")] = 3
    valid_same_team[HEADERS.index("PLAYER_NAME")] = "Player 3"
    valid_same_team[HEADERS.index("MATCHUP")] = "CHI @ BOS"
    rows.insert(1, valid_same_team)
    return json.dumps(document).encode()


def _designation_payload(season, game_type):
    document = json.loads(_payload(season, game_type))
    rows = document["resultSets"][0]["rowSet"]
    rows[1][HEADERS.index("MATCHUP")] = "BOS @ CHI"
    return json.dumps(document).encode()


def _metadata(season, game_type):
    return {
        "provider": "stats_nba",
        "endpoint": "LeagueGameLog",
        "season": season,
        "season_label": f"{season - 1}-{season % 100:02d}",
        "game_type": game_type,
        "season_type": {
            "regular": "Regular Season", "playoff": "Playoffs",
        }[game_type],
        "league_id": "00",
        "player_or_team_abbreviation": "P",
    }


def _publish_scope(data_dir, seasons=(1984, 1985)):
    for season in seasons:
        for game_type in ("regular", "playoff"):
            destination = player_logs.cache_path(data_dir, season, game_type)
            cache.publish_bytes(
                destination,
                _payload(season, game_type),
                _metadata(season, game_type),
                player_logs.validate_payload,
            )


def _use_tiny_scope(monkeypatch):
    for module in (player_games_build, player_logs):
        monkeypatch.setattr(module, "SOURCE_SEASONS", (1984, 1985))
        monkeypatch.setattr(module, "PUBLICATION_SEASONS", (1984, 1985))
        monkeypatch.setattr(module, "PLAY_IN_SEASONS", ())


def _use_one_season_scope(monkeypatch):
    for module in (player_games_build, player_logs):
        monkeypatch.setattr(module, "SOURCE_SEASONS", (1984,))
        monkeypatch.setattr(module, "PUBLICATION_SEASONS", (1984,))
        monkeypatch.setattr(module, "PLAY_IN_SEASONS", ())


def _use_empty_exception_ledgers(monkeypatch, data_dir):
    """Point matchup/designation ledgers at empty fixtures under a tiny scope.

    Would fail if a tiny-scoped season range rejected the committed
    matchup/designation ledgers' real (wider-range) entries: those ledgers
    are validated against the patched SOURCE_SEASONS, so any test that
    narrows the season scope and then loads the real, unpatched ledgers
    would see spurious "season outside" errors unrelated to what it's
    testing.
    """
    matchup_ledger = data_dir / "fixture-matchup-exceptions.json"
    matchup_ledger.write_bytes(b'{"exceptions":{},"schema_version":1}\n')
    monkeypatch.setattr(
        player_games_build,
        "MATCHUP_EXCEPTIONS_PATH",
        matchup_ledger,
        raising=False,
    )
    designation_ledger = data_dir / "fixture-designation-exceptions.json"
    designation_ledger.write_bytes(EMPTY_DESIGNATION_LEDGER_BYTES)
    monkeypatch.setattr(
        player_games_build,
        "DESIGNATION_EXCEPTIONS_PATH",
        designation_ledger,
        raising=False,
    )
    # `load_matchup_exceptions`/`load_designation_exceptions` bind their
    # default `path=` argument at definition time, so patching the module
    # path attributes above does not affect callers (like `_assemble_season`
    # via its own None-default) that omit an explicit path. Patch the
    # loaders directly so every fallback path sees the empty fixtures too.
    monkeypatch.setattr(player_logs, "load_matchup_exceptions", lambda *a, **k: {})
    monkeypatch.setattr(
        player_logs, "load_designation_exceptions", lambda *a, **k: {}
    )
    return matchup_ledger, designation_ledger


def _publish_references(data_dir, seasons=(1984, 1985)):
    parquet_dir = data_dir / "parquet"
    parquet_dir.mkdir(parents=True, exist_ok=True)
    regular_games = []
    regular_team_games = []
    postseason_games = []
    for season in seasons:
        year = season - 1
        game_date = pd.Timestamp(f"{year}-10-28").date()
        regular_id = f"002{str(year)[-2:]}00001"
        regular_games.append({
            "game_id": regular_id,
            "season": season,
            "game_date": game_date,
            "home_franchise_id": "BOS",
            "away_franchise_id": "CHI",
            "home_pts": 2,
            "away_pts": 2,
            "neutral_site": False,
            "neutral_site_source": None,
            "venue_name": None,
            "venue_city": None,
        })
        regular_team_games.extend([
            {
                "game_id": regular_id,
                "season": season,
                "game_date": game_date,
                "franchise_id": "BOS",
                "opponent_franchise_id": "CHI",
                "is_home": True,
                "pts": 2,
            },
            {
                "game_id": regular_id,
                "season": season,
                "game_date": game_date,
                "franchise_id": "CHI",
                "opponent_franchise_id": "BOS",
                "is_home": False,
                "pts": 2,
            },
        ])
        postseason_games.append({
            "game_id": f"bref-{year}1028BOS",
            "season": season,
            "game_date": game_date,
            "game_type": "playoff",
            "home_franchise_id": "BOS",
            "away_franchise_id": "CHI",
            "home_pts": 2,
            "away_pts": 2,
            "overtime_periods": 0,
        })
    pd.DataFrame(regular_games).to_parquet(
        parquet_dir / "games.parquet", index=False
    )
    pd.DataFrame(regular_team_games).to_parquet(
        parquet_dir / "team_games.parquet", index=False
    )
    pd.DataFrame(postseason_games).to_parquet(
        parquet_dir / "playoff_games.parquet", index=False
    )
    (parquet_dir / "build_manifest.json").write_text(
        '{"fixture":"modern"}\n', encoding="utf-8"
    )
    (parquet_dir / "playoff_games_manifest.json").write_text(
        '{"fixture":"playoffs"}\n', encoding="utf-8"
    )
    return parquet_dir


def _write_reviewed_fixture_ledger(monkeypatch, data_dir):
    observed = player_games_build.audit_source(data_dir)["discrepancies"]
    approved = {
        section: {
            key: {
                **entry,
                "note": "Reviewed fixture evidence; raw source semantics are retained.",
            }
            for key, entry in entries.items()
        }
        for section, entries in observed.items()
    }
    ledger = data_dir / "fixture-exceptions.json"
    ledger.write_text(
        json.dumps(approved, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(player_games_build, "EXCEPTIONS_PATH", ledger)
    return ledger


def _publish_scope_with_exact_minutes(data_dir, seasons=(1984, 1985)):
    """Publish the shared fixture payload with per-team minutes at 240.

    Would fail if the ordinary build observed a spurious minute discrepancy:
    the shared ``_payload`` fixture puts a single 24-minute player on each
    team, which is far outside the minute tolerance. This variant rewrites
    MIN to 240 so the ordinary build sees zero discrepancies.
    """
    for season in seasons:
        for game_type in ("regular", "playoff"):
            document = json.loads(_payload(season, game_type))
            result_set = document["resultSets"][0]
            min_index = result_set["headers"].index("MIN")
            for row in result_set["rowSet"]:
                row[min_index] = 240
            payload = json.dumps(document).encode()
            cache.publish_bytes(
                player_logs.cache_path(data_dir, season, game_type),
                payload,
                _metadata(season, game_type),
                player_logs.validate_payload,
            )


def _write_empty_ledger(monkeypatch, data_dir):
    ledger = data_dir / "fixture-exceptions.json"
    ledger.write_text(
        json.dumps(_ledger(), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(player_games_build, "EXCEPTIONS_PATH", ledger)
    return ledger


def _seed_build_inputs(monkeypatch, data_dir):
    _use_tiny_scope(monkeypatch)
    _publish_scope_with_exact_minutes(data_dir)
    references = _publish_references(data_dir)
    _use_empty_exception_ledgers(monkeypatch, data_dir)
    ledger = _write_empty_ledger(monkeypatch, data_dir)
    return data_dir / "raw/stats_nba/player_logs", references, ledger


def _seed_matchup_build_inputs(monkeypatch, data_dir):
    _use_one_season_scope(monkeypatch)
    for game_type in ("regular", "playoff"):
        destination = player_logs.cache_path(data_dir, 1984, game_type)
        payload = (
            _matchup_payload(1984, game_type)
            if game_type == "regular"
            else _payload(1984, game_type)
        )
        cache.publish_bytes(
            destination,
            payload,
            _metadata(1984, game_type),
            player_logs.validate_payload,
        )
    references = _publish_references(data_dir, seasons=(1984,))
    games = pd.read_parquet(references / "games.parquet")
    games.loc[games.game_id.eq("0028300001"), "away_pts"] = 4
    games.to_parquet(references / "games.parquet", index=False)
    team_games = pd.read_parquet(references / "team_games.parquet")
    team_games.loc[
        team_games.game_id.eq("0028300001")
        & team_games.franchise_id.eq("CHI"),
        "pts",
    ] = 4
    team_games.to_parquet(references / "team_games.parquet", index=False)
    matchup_ledger = data_dir / "fixture-matchup-exceptions.json"
    matchup_ledger.write_bytes(MATCHUP_FIXTURE_LEDGER_BYTES)
    monkeypatch.setattr(
        player_games_build,
        "MATCHUP_EXCEPTIONS_PATH",
        matchup_ledger,
        raising=False,
    )
    designation_ledger = data_dir / "fixture-designation-exceptions.json"
    designation_ledger.write_bytes(EMPTY_DESIGNATION_LEDGER_BYTES)
    monkeypatch.setattr(
        player_games_build,
        "DESIGNATION_EXCEPTIONS_PATH",
        designation_ledger,
        raising=False,
    )
    ledger = _write_reviewed_fixture_ledger(monkeypatch, data_dir)
    return (
        data_dir / "raw/stats_nba/player_logs",
        references,
        ledger,
        matchup_ledger,
    )


def _seed_designation_build_inputs(monkeypatch, data_dir):
    _use_one_season_scope(monkeypatch)
    for game_type in ("regular", "playoff"):
        destination = player_logs.cache_path(data_dir, 1984, game_type)
        payload = (
            _designation_payload(1984, game_type)
            if game_type == "regular"
            else _payload(1984, game_type)
        )
        cache.publish_bytes(
            destination,
            payload,
            _metadata(1984, game_type),
            player_logs.validate_payload,
        )
    references = _publish_references(data_dir, seasons=(1984,))
    games = pd.read_parquet(references / "games.parquet")
    games.loc[games.game_id.eq("0028300001"), "neutral_site"] = True
    games.loc[games.game_id.eq("0028300001"), "neutral_site_source"] = "hoopr"
    games.loc[games.game_id.eq("0028300001"), "venue_name"] = "Fixture Arena"
    games.loc[games.game_id.eq("0028300001"), "venue_city"] = "Fixture City"
    games.to_parquet(references / "games.parquet", index=False)

    matchup_ledger = data_dir / "fixture-matchup-exceptions.json"
    matchup_ledger.write_bytes(b'{"exceptions":{},"schema_version":1}\n')
    monkeypatch.setattr(
        player_games_build,
        "MATCHUP_EXCEPTIONS_PATH",
        matchup_ledger,
        raising=False,
    )
    designation_ledger = data_dir / "fixture-designation-exceptions.json"
    designation_ledger.write_bytes(DESIGNATION_FIXTURE_LEDGER_BYTES)
    monkeypatch.setattr(
        player_games_build,
        "DESIGNATION_EXCEPTIONS_PATH",
        designation_ledger,
        raising=False,
    )
    ledger = _write_reviewed_fixture_ledger(monkeypatch, data_dir)
    return (
        data_dir / "raw/stats_nba/player_logs",
        references,
        ledger,
        matchup_ledger,
        designation_ledger,
    )


def _replace_fixture_franchise(references, ledger, old, new):
    for name in ("games.parquet", "team_games.parquet", "playoff_games.parquet"):
        path = references / name
        frame = pd.read_parquet(path)
        for column in frame.columns:
            if "franchise_id" in column:
                frame[column] = frame[column].replace(old, new)
        frame.to_parquet(path, index=False)
    document = json.loads(ledger.read_text(encoding="utf-8"))
    for section in ("minutes", "points"):
        rewritten = {}
        for key, entry in document[section].items():
            new_key = key.replace(f"|{old}", f"|{new}")
            rewritten[new_key] = {
                **entry,
                "franchise_id": (
                    new if entry["franchise_id"] == old
                    else entry["franchise_id"]
                ),
            }
        document[section] = rewritten
    ledger.write_text(
        json.dumps(document, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _seed_release_scoped_repair_inputs(monkeypatch, data_dir):
    """Publish two seasons where only the newer one is in publication scope.

    Season 1984 carries an approved historical matchup exception but is
    quarantined out of PUBLICATION_SEASONS; season 1985 carries a
    publication-scope approved designation exception. Would fail if repair
    application ignored PUBLICATION_SEASONS scoping and applied every
    approved ledger entry regardless of season.
    """
    for module in (player_games_build, player_logs):
        monkeypatch.setattr(module, "SOURCE_SEASONS", (1984, 1985))
        monkeypatch.setattr(module, "PUBLICATION_SEASONS", (1985,))
        monkeypatch.setattr(module, "PLAY_IN_SEASONS", ())

    def _rowset(payload):
        document = json.loads(payload)
        return document, document["resultSets"][0]["rowSet"]

    def _publish(season, game_type, document):
        cache.publish_bytes(
            player_logs.cache_path(data_dir, season, game_type),
            json.dumps(document).encode(),
            _metadata(season, game_type),
            player_logs.validate_payload,
        )

    min_index = HEADERS.index("MIN")

    # 1984 (quarantined/historical): approved matchup anomaly, exact minutes.
    document, rows = _rowset(_matchup_payload(1984, "regular"))
    rows[0][min_index] = 120
    rows[1][min_index] = 120
    rows[2][min_index] = 240
    _publish(1984, "regular", document)
    document, rows = _rowset(_payload(1984, "playoff"))
    for row in rows:
        row[min_index] = 240
    _publish(1984, "playoff", document)

    # 1985 (published): approved designation anomaly, exact minutes.
    document, rows = _rowset(_designation_payload(1985, "regular"))
    for row in rows:
        row[min_index] = 240
    _publish(1985, "regular", document)
    document, rows = _rowset(_payload(1985, "playoff"))
    for row in rows:
        row[min_index] = 240
    _publish(1985, "playoff", document)

    references = _publish_references(data_dir, seasons=(1984, 1985))
    games = pd.read_parquet(references / "games.parquet")
    games.loc[games.game_id.eq("0028300001"), "away_pts"] = 4
    games.loc[games.game_id.eq("0028400001"), "neutral_site"] = True
    games.loc[games.game_id.eq("0028400001"), "neutral_site_source"] = "hoopr"
    games.loc[games.game_id.eq("0028400001"), "venue_name"] = "Fixture Arena"
    games.loc[games.game_id.eq("0028400001"), "venue_city"] = "Fixture City"
    games.to_parquet(references / "games.parquet", index=False)
    team_games = pd.read_parquet(references / "team_games.parquet")
    team_games.loc[
        team_games.game_id.eq("0028300001") & team_games.franchise_id.eq("CHI"),
        "pts",
    ] = 4
    team_games.to_parquet(references / "team_games.parquet", index=False)

    matchup_ledger = data_dir / "fixture-matchup-exceptions.json"
    matchup_ledger.write_bytes(MATCHUP_FIXTURE_LEDGER_BYTES)
    monkeypatch.setattr(
        player_games_build,
        "MATCHUP_EXCEPTIONS_PATH",
        matchup_ledger,
        raising=False,
    )

    designation_entry = {
        **DESIGNATION_FIXTURE_ENTRY,
        "season": 1985,
        "game_id": "0028400001",
        "game_date": "1984-10-28",
    }
    designation_key = "1985|regular|0028400001"
    designation_ledger = data_dir / "fixture-designation-exceptions.json"
    designation_ledger.write_bytes(
        (
            json.dumps(
                {
                    "schema_version": 1,
                    "designations": {designation_key: designation_entry},
                },
                sort_keys=True,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")
    )
    monkeypatch.setattr(
        player_games_build,
        "DESIGNATION_EXCEPTIONS_PATH",
        designation_ledger,
        raising=False,
    )

    ledger = _write_reviewed_fixture_ledger(monkeypatch, data_dir)
    return (
        data_dir / "raw/stats_nba/player_logs",
        references,
        ledger,
        matchup_ledger,
        designation_ledger,
    )


def _build_scoped_publication(
    tmp_path, monkeypatch, *, source_seasons, publication_seasons
):
    """Build a real publication for an explicit source/publication split.

    Would fail if manifest construction, layout, and independent validation
    could only be exercised where SOURCE_SEASONS and PUBLICATION_SEASONS
    happen to coincide (as with the tiny/one-season scope helpers); this
    drives them apart with a quarantined source season so publication
    scoping is proven against literal expected values, not derived from
    production scope constants. Patches through the caller's own
    ``monkeypatch`` fixture so the scope reverts at test teardown instead of
    leaking into later tests.
    """
    for module in (player_games_build, player_logs):
        monkeypatch.setattr(module, "SOURCE_SEASONS", tuple(source_seasons))
        monkeypatch.setattr(
            module, "PUBLICATION_SEASONS", tuple(publication_seasons)
        )
        monkeypatch.setattr(module, "PLAY_IN_SEASONS", ())
    _publish_scope_with_exact_minutes(tmp_path, seasons=source_seasons)
    _publish_references(tmp_path, seasons=publication_seasons)
    _use_empty_exception_ledgers(monkeypatch, tmp_path)
    _write_empty_ledger(monkeypatch, tmp_path)
    stage = tmp_path / "stage"
    stage.mkdir()
    manifest = player_games_build.build_from_roots(
        tmp_path / "raw/stats_nba/player_logs", tmp_path / "parquet", stage,
    )
    return stage, manifest


def test_player_games_arrow_schema_has_exact_order_types_and_nullability():
    """Would fail if a partition could drift from the player-game contract."""
    expected = pa.schema(
        [
            pa.field("game_id", pa.large_string(), nullable=False),
            pa.field("person_id", pa.int64(), nullable=False),
            pa.field("season", pa.int64(), nullable=False),
            pa.field("game_type", pa.large_string(), nullable=False),
            pa.field("game_date", pa.date32(), nullable=False),
            pa.field("player_name", pa.large_string(), nullable=False),
            pa.field("nba_team_id", pa.int64(), nullable=False),
            pa.field("team_name_raw", pa.large_string(), nullable=False),
            pa.field("team_abbreviation_raw", pa.large_string(), nullable=False),
            pa.field("matchup_raw", pa.large_string(), nullable=False),
            pa.field("franchise_id", pa.large_string(), nullable=False),
            pa.field("opponent_franchise_id", pa.large_string(), nullable=False),
            pa.field("is_home", pa.bool_(), nullable=False),
            pa.field("min", pa.int64()),
            pa.field("pts", pa.int64()),
            pa.field("fgm", pa.int64()),
            pa.field("fga", pa.int64()),
            pa.field("fg3m", pa.int64()),
            pa.field("fg3a", pa.int64()),
            pa.field("ftm", pa.int64()),
            pa.field("fta", pa.int64()),
            pa.field("oreb", pa.int64()),
            pa.field("dreb", pa.int64()),
            pa.field("reb", pa.int64()),
            pa.field("ast", pa.int64()),
            pa.field("stl", pa.int64()),
            pa.field("blk", pa.int64()),
            pa.field("tov", pa.int64()),
            pa.field("pf", pa.int64()),
            pa.field("plus_minus", pa.int64()),
        ]
    )

    assert player_games_build.PLAYER_GAMES_ARROW_SCHEMA == expected
    assert expected.names == player_logs.PLAYER_GAME_COLUMNS


def test_assemble_seasons_reads_complete_cache_with_exact_schema_and_order(
    monkeypatch, tmp_path,
):
    """Would fail if cache rows were inferred, omitted, or unstably ordered."""
    _use_tiny_scope(monkeypatch)
    _publish_scope(tmp_path)
    _use_empty_exception_ledgers(monkeypatch, tmp_path)

    table = player_games_build._assemble_seasons(
        tmp_path, seasons=player_games_build.SOURCE_SEASONS
    )

    assert table.schema == player_games_build.PLAYER_GAMES_ARROW_SCHEMA
    assert table.column_names == player_logs.PLAYER_GAME_COLUMNS
    keys = table.to_pandas()[
        ["season", "game_date", "game_id", "franchise_id", "person_id"]
    ]
    assert keys.equals(
        keys.sort_values(
            ["season", "game_date", "game_id", "franchise_id", "person_id"],
            kind="stable",
        ).reset_index(drop=True)
    )


def test_assemble_seasons_reads_only_the_requested_scope(monkeypatch, tmp_path):
    """Would fail if assembly silently read a wider scope than requested."""
    requested = []

    def fake_assemble(_data_dir, season, *args, **kwargs):
        requested.append(season)
        return pa.table({"season": [season]})

    monkeypatch.setattr(player_games_build, "_assemble_season", fake_assemble)
    player_games_build._assemble_seasons(
        tmp_path,
        seasons=(2012, 2013),
        matchup_exceptions={},
        designation_exceptions={},
    )
    assert requested == [2012, 2013]


@pytest.mark.parametrize(
    ("column", "value", "match"),
    [
        ("player_name", None, "required columns contain nulls"),
        ("person_id", 1.5, "Arrow conversion"),
    ],
)
def test_table_conversion_rejects_required_nulls_and_unsafe_physical_values(
    column, value, match,
):
    """Would fail if Arrow conversion weakened nullability or safe casting."""
    frame = player_logs.normalize_payload(
        _payload(1984, "regular"), season=1984, game_type="regular"
    )
    if column == "person_id":
        frame[column] = frame[column].astype("float64")
    frame.loc[0, column] = value

    with pytest.raises(player_games_build.PlayerGamesBuildError, match=match):
        player_games_build._table_from_frame(frame)


def test_assemble_season_rejects_duplicate_keys_across_game_types(
    monkeypatch,
):
    """Would fail if per-payload uniqueness hid a cross-type key collision."""
    frame = player_logs.normalize_payload(
        _payload(1984, "regular"), season=1984, game_type="regular"
    )
    monkeypatch.setattr(
        player_games_build.player_logs,
        "read_cached",
        lambda *args: (b"payload", {}),
    )
    monkeypatch.setattr(
        player_games_build.player_logs,
        "normalize_payload",
        lambda *args, **kwargs: frame.copy(),
    )

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match=r"duplicate \(game_id, person_id\)",
    ):
        player_games_build._assemble_season(Path("unused"), 1984)


@pytest.mark.parametrize("mutation", ["schema", "rows", "compression"])
def test_validate_partition_rejects_reread_drift(tmp_path, mutation):
    """Would fail if staged bytes were trusted without exact reread checks."""
    frame = player_logs.normalize_payload(
        _payload(1984, "regular"), season=1984, game_type="regular"
    )
    table = player_games_build._table_from_frame(frame)
    path = tmp_path / "player_games.parquet"
    written = table
    compression = "zstd"
    if mutation == "schema":
        written = table.set_column(
            0,
            "game_id",
            pa.array(table["game_id"].to_pylist(), type=pa.string()),
        )
    elif mutation == "rows":
        written = table.slice(0, table.num_rows - 1)
    else:
        compression = "snappy"
    pq.write_table(
        written,
        path,
        compression=compression,
        use_dictionary=False,
        write_statistics=True,
    )

    with pytest.raises(player_games_build.PlayerGamesBuildError):
        player_games_build._validate_partition(table, path, season=1984)


def test_default_exception_ledger_is_exact_and_empty():
    """Would fail if the shipped validation exceptions were pre-populated."""
    assert player_games_build.EXCEPTIONS_PATH.read_bytes() == (
        b'{\n  "minutes": {},\n  "points": {}\n}\n'
    )
    assert player_games_build.load_exceptions() == {
        "minutes": {},
        "points": {},
    }


def _points_exception():
    return {
        "game_id": "0028300001",
        "franchise_id": "BOS",
        "player_total": 100,
        "published_total": 101,
        "season": 1984,
        "note": "Published total differs from player rows.",
    }


def _minutes_exception():
    return {
        "game_id": "0028300002",
        "franchise_id": "CHI",
        "player_total": 240,
        "opponent_total": 240,
        "inferred_overtime_periods": 0,
        "published_overtime_periods": 0,
        "reason": "source totals are internally inconsistent",
        "season": 1984,
        "note": "Reviewed against the published box score.",
    }


def _ledger(*, minutes=None, points=None):
    return {
        "minutes": {} if minutes is None else minutes,
        "points": {} if points is None else points,
    }


def _save_ledger(path, document):
    path.write_text(json.dumps(document), encoding="utf-8")


@pytest.mark.parametrize(
    ("document", "match"),
    [
        ([], "root must be an object"),
        ({"minutes": {}}, "top-level keys"),
        ({"minutes": {}, "points": {}, "extra": {}}, "top-level keys"),
        ({"minutes": [], "points": {}}, "minutes must be an object"),
        (_ledger(points={"bad": _points_exception()}), "malformed composite key"),
        (
            _ledger(points={"0028300001|BOS": {
                key: value for key, value in _points_exception().items()
                if key != "note"
            }}),
            "fields",
        ),
        (
            _ledger(points={"0028300001|BOS": {
                **_points_exception(), "reason": "unexpected",
            }}),
            "fields",
        ),
        (
            _ledger(minutes={"0028300002|CHI": {
                key: value for key, value in _minutes_exception().items()
                if key != "reason"
            }}),
            "fields",
        ),
    ],
)
def test_load_exceptions_rejects_invalid_structure(tmp_path, document, match):
    """Would fail if malformed ledger structure reached validation logic."""
    path = tmp_path / "exceptions.json"
    _save_ledger(path, document)

    with pytest.raises(player_games_build.PlayerGamesBuildError, match=match):
        player_games_build.load_exceptions(path)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("points", "game_id", 28300001),
        ("points", "franchise_id", 38),
        ("points", "player_total", True),
        ("points", "published_total", None),
        ("points", "season", True),
        ("points", "note", 1),
        ("minutes", "player_total", "240"),
        ("minutes", "opponent_total", False),
        ("minutes", "inferred_overtime_periods", 1.0),
        ("minutes", "published_overtime_periods", True),
        ("minutes", "reason", None),
    ],
)
def test_load_exceptions_rejects_wrong_field_types(
    tmp_path, section, field, value,
):
    """Would fail if bools or coercible values passed exact ledger types."""
    entry = _points_exception() if section == "points" else _minutes_exception()
    key = f"{entry['game_id']}|{entry['franchise_id']}"
    entry[field] = value
    path = tmp_path / "exceptions.json"
    _save_ledger(path, _ledger(**{section: {key: entry}}))

    with pytest.raises(player_games_build.PlayerGamesBuildError, match="wrong type"):
        player_games_build.load_exceptions(path)


@pytest.mark.parametrize("section", ["points", "minutes"])
def test_load_exceptions_requires_nonempty_notes(tmp_path, section):
    """Would fail if a non-empty exception lacked review rationale."""
    entry = _points_exception() if section == "points" else _minutes_exception()
    entry["note"] = "  "
    key = f"{entry['game_id']}|{entry['franchise_id']}"
    path = tmp_path / "exceptions.json"
    _save_ledger(path, _ledger(**{section: {key: entry}}))

    with pytest.raises(player_games_build.PlayerGamesBuildError, match="non-empty note"):
        player_games_build.load_exceptions(path)


def test_load_exceptions_requires_key_body_identity(tmp_path):
    """Would fail if an exception could apply to a different semantic key."""
    path = tmp_path / "exceptions.json"
    _save_ledger(
        path,
        _ledger(points={"0028300001|CHI": _points_exception()}),
    )

    with pytest.raises(player_games_build.PlayerGamesBuildError, match="identity"):
        player_games_build.load_exceptions(path)


@pytest.mark.parametrize("text", ["{", '{"minutes": NaN, "points": {}}'])
def test_load_exceptions_rejects_invalid_json(tmp_path, text):
    """Would fail if malformed or non-standard JSON were accepted."""
    path = tmp_path / "exceptions.json"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(player_games_build.PlayerGamesBuildError, match="invalid JSON"):
        player_games_build.load_exceptions(path)


def test_load_exceptions_rejects_duplicate_semantic_identities(tmp_path):
    """Would fail if JSON duplicate keys silently replaced reviewed evidence."""
    entry = json.dumps(_points_exception())
    path = tmp_path / "exceptions.json"
    path.write_text(
        '{"minutes": {}, "points": {'
        f'"0028300001|BOS": {entry}, "0028300001|BOS": {entry}'
        "}}",
        encoding="utf-8",
    )

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match="duplicate semantic identity",
    ):
        player_games_build.load_exceptions(path)


def _player_game_rows():
    games = [
        (
            "0028300001", 1984, "regular", "1983-10-28",
            "BOS", "CHI", 108, 107,
        ),
        (
            "0048300001", 1984, "playoff", "1984-04-20",
            "BOS", "CHI", 110, 100,
        ),
        (
            "0051900001", 2020, "play_in", "2020-08-15",
            "LAL", "POR", 126, 122,
        ),
    ]
    rows = []
    person_id = 1
    for game_id, season, game_type, game_date, home, away, home_pts, away_pts in games:
        for franchise_id, opponent_id, is_home, total in (
            (home, away, True, home_pts),
            (away, home, False, away_pts),
        ):
            rows.extend(
                {
                    "game_id": game_id,
                    "person_id": person_id + offset,
                    "season": season,
                    "game_type": game_type,
                    "game_date": pd.Timestamp(game_date).date(),
                    "franchise_id": franchise_id,
                    "opponent_franchise_id": opponent_id,
                    "is_home": is_home,
                    "min": 120,
                    "pts": points,
                }
                for offset, points in enumerate((total // 2, total - total // 2))
            )
            person_id += 2
    return pd.DataFrame(rows)


def _coverage_references():
    games = pd.DataFrame(
        [{
            "game_id": "0028300001",
            "season": 1984,
            "game_date": pd.Timestamp("1983-10-28").date(),
            "home_franchise_id": "BOS",
            "away_franchise_id": "CHI",
            "home_pts": 108,
            "away_pts": 107,
        }]
    )
    team_games = pd.DataFrame(
        [
            {
                "game_id": "0028300001", "season": 1984,
                "game_date": pd.Timestamp("1983-10-28").date(),
                "franchise_id": "BOS", "opponent_franchise_id": "CHI",
                "is_home": True, "pts": 108,
            },
            {
                "game_id": "0028300001", "season": 1984,
                "game_date": pd.Timestamp("1983-10-28").date(),
                "franchise_id": "CHI", "opponent_franchise_id": "BOS",
                "is_home": False, "pts": 107,
            },
        ]
    )
    playoff_games = pd.DataFrame(
        [
            {
                "game_id": "bref-198404200BOS", "season": 1984,
                "game_date": pd.Timestamp("1984-04-20").date(),
                "game_type": "playoff", "home_franchise_id": "BOS",
                "away_franchise_id": "CHI", "home_pts": 110,
                "away_pts": 100, "overtime_periods": 0,
            },
            {
                "game_id": "bref-202008150LAL", "season": 2020,
                "game_date": pd.Timestamp("2020-08-15").date(),
                "game_type": "play_in", "home_franchise_id": "LAL",
                "away_franchise_id": "POR", "home_pts": 126,
                "away_pts": 122, "overtime_periods": 0,
            },
            {
                "game_id": "bref-198404190NYK", "season": 1984,
                "game_date": pd.Timestamp("1984-04-19").date(),
                "game_type": "voided", "home_franchise_id": "NYK",
                "away_franchise_id": "PHI", "home_pts": 0,
                "away_pts": 0, "overtime_periods": 0,
            },
        ]
    )
    return games, team_games, playoff_games


def test_cross_validate_coverage_uses_explicit_publication_seasons():
    """Would fail if coverage still read a hard-coded season scope."""
    aggregates = player_games_build.aggregate_team_games(_player_game_rows())
    games, team_games, playoffs = _coverage_references()

    validated = player_games_build.cross_validate_coverage(
        aggregates,
        games,
        team_games,
        playoffs,
        seasons=(2020,),
    )

    assert set(validated["season"]) == {2020}
    assert len(validated) == 2


def _validated_coverage(seasons=(1984, 2020)):
    aggregates = player_games_build.aggregate_team_games(_player_game_rows())
    return player_games_build.cross_validate_coverage(
        aggregates, *_coverage_references(), seasons=seasons
    )


def test_exact_regular_and_postseason_coverage_succeeds():
    """Would fail if valid team rows were lost or source IDs were joined across providers."""
    validated = _validated_coverage()

    assert validated[["game_id", "franchise_id"]].to_records(index=False).tolist() == [
        ("0028300001", "BOS"),
        ("0028300001", "CHI"),
        ("0048300001", "BOS"),
        ("0048300001", "CHI"),
        ("0051900001", "LAL"),
        ("0051900001", "POR"),
    ]
    assert validated["published_total"].tolist() == [108, 107, 110, 100, 126, 122]


def test_regular_coverage_rejects_missing_player_game():
    """Would fail if an outer-join publication-only row were discarded."""
    aggregates = player_games_build.aggregate_team_games(_player_game_rows())
    aggregates = aggregates.loc[
        ~(
            aggregates["game_id"].eq("0028300001")
            & aggregates["franchise_id"].eq("CHI")
        )
    ]

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match=r"0028300001\|CHI",
    ):
        player_games_build.cross_validate_coverage(
            aggregates, *_coverage_references(), seasons=(1984, 2020),
        )


def test_regular_coverage_rejects_extra_player_game():
    """Would fail if an outer-join player-only row were discarded."""
    aggregates = player_games_build.aggregate_team_games(_player_game_rows())
    extra = aggregates.iloc[[0]].copy()
    extra["game_id"] = "0028300999"
    aggregates = pd.concat([aggregates, extra], ignore_index=True)

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match=r"0028300999\|BOS",
    ):
        player_games_build.cross_validate_coverage(
            aggregates, *_coverage_references(), seasons=(1984, 2020),
        )


def test_regular_coverage_rejects_team_game_identity_drift():
    """Would fail if the team-game opponent identity were not checked."""
    games, team_games, playoff_games = _coverage_references()
    team_games.loc[team_games["franchise_id"].eq("BOS"), "opponent_franchise_id"] = "NYK"

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match=r"0028300001\|BOS",
    ):
        player_games_build.cross_validate_coverage(
            player_games_build.aggregate_team_games(_player_game_rows()),
            games,
            team_games,
            playoff_games,
            seasons=(1984, 2020),
        )


def test_regular_coverage_duplicate_game_reports_identity():
    """Would fail if duplicate diagnostics assumed a postseason key shape."""
    games, team_games, playoff_games = _coverage_references()
    games = pd.concat([games, games], ignore_index=True)

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match=r"duplicate regular game identity.*0028300001",
    ):
        player_games_build.cross_validate_coverage(
            player_games_build.aggregate_team_games(_player_game_rows()),
            games,
            team_games,
            playoff_games,
            seasons=(1984, 2020),
        )


def test_postseason_coverage_joins_without_source_game_id():
    """Would fail if NBA Stats postseason IDs were compared with Basketball Reference IDs."""
    validated = _validated_coverage()

    assert set(validated.loc[validated["game_type"].ne("regular"), "game_id"]) == {
        "0048300001", "0051900001",
    }


def test_postseason_coverage_rejects_duplicate_composite_key():
    """Would fail if an ambiguous Basketball Reference composite identity were accepted."""
    games, team_games, playoff_games = _coverage_references()
    playoff_games = pd.concat(
        [playoff_games, playoff_games.iloc[[0]]], ignore_index=True
    )

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match=r"1984.*1984-04-20.*BOS.*CHI",
    ):
        player_games_build.cross_validate_coverage(
            player_games_build.aggregate_team_games(_player_game_rows()),
            games,
            team_games,
            playoff_games,
            seasons=(1984, 2020),
        )


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("game_date", pd.Timestamp("1984-04-21").date()),
        ("home_franchise_id", "NYK"),
        ("away_franchise_id", "PHI"),
    ],
)
def test_postseason_coverage_rejects_home_away_or_date_drift(column, value):
    """Would fail if any field in the postseason composite identity were ignored."""
    games, team_games, playoff_games = _coverage_references()
    playoff_games.loc[playoff_games["game_type"].eq("playoff"), column] = value

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match=r"0048300001|1984.*1984-04-20.*BOS.*CHI",
    ):
        player_games_build.cross_validate_coverage(
            player_games_build.aggregate_team_games(_player_game_rows()),
            games,
            team_games,
            playoff_games,
            seasons=(1984, 2020),
        )


def _approved(observed):
    return {
        section: {
            key: {**entry, "note": "Reviewed against the published box score."}
            for key, entry in entries.items()
        }
        for section, entries in observed.items()
    }


def _regular_discrepancy_frame(home_minutes=240, away_minutes=240):
    frame = _validated_coverage().loc[
        lambda rows: rows["game_id"].eq("0028300001")
    ].copy()
    frame.loc[frame["franchise_id"].eq("BOS"), "minute_total"] = home_minutes
    frame.loc[frame["franchise_id"].eq("CHI"), "minute_total"] = away_minutes
    frame["minutes_complete"] = frame["minute_total"].notna()
    return frame


def test_point_discrepancies_are_empty_for_exact_totals():
    """Would fail if exact published/player point equality became review noise."""
    assert player_games_build.collect_discrepancies(_validated_coverage())["points"] == {}


def test_point_discrepancy_rejects_unlisted_mismatch_with_both_totals():
    """Would fail if an observed point mismatch could bypass review."""
    validated = _regular_discrepancy_frame()
    validated.loc[validated["franchise_id"].eq("BOS"), "player_total"] = 108
    validated.loc[validated["franchise_id"].eq("BOS"), "published_total"] = 107
    observed = player_games_build.collect_discrepancies(validated)

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match=r"0028300001\|BOS.*108.*107",
    ):
        player_games_build.validate_exceptions(observed, _ledger())


def test_point_discrepancy_accepts_exact_approved_mismatch():
    """Would fail if an exactly reviewed mismatch were rejected."""
    validated = _regular_discrepancy_frame()
    validated.loc[validated["franchise_id"].eq("BOS"), "player_total"] = 108
    validated.loc[validated["franchise_id"].eq("BOS"), "published_total"] = 107
    observed = player_games_build.collect_discrepancies(validated)

    player_games_build.validate_exceptions(observed, _approved(observed))


def test_point_discrepancy_rejects_approved_value_drift():
    """Would fail if review approval applied after either total changed."""
    validated = _regular_discrepancy_frame()
    validated.loc[validated["franchise_id"].eq("BOS"), "player_total"] = 108
    validated.loc[validated["franchise_id"].eq("BOS"), "published_total"] = 107
    observed = player_games_build.collect_discrepancies(validated)
    approved = _approved(observed)
    approved["points"]["0028300001|BOS"]["player_total"] = 109

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match=r"0028300001\|BOS.*108.*107.*109",
    ):
        player_games_build.validate_exceptions(observed, approved)


def test_point_discrepancy_rejects_stale_approved_key():
    """Would fail if obsolete point exceptions remained silently approved."""
    approved = _ledger(points={"0028300001|BOS": _points_exception()})

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match=r"stale.*0028300001\|BOS",
    ):
        player_games_build.validate_exceptions(_ledger(), approved)


def test_validate_exceptions_validates_approved_ledger_first():
    """Would fail if malformed observed candidates hid an invalid reviewed ledger."""
    approved_entry = _points_exception()
    approved_entry["note"] = " "

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match=r"requires a non-empty note",
    ):
        player_games_build.validate_exceptions(
            _ledger(points={"0028300001|BOS": {"game_id": "bad"}}),
            _ledger(points={"0028300001|BOS": approved_entry}),
        )


def test_null_player_points_become_a_canonical_discrepancy():
    """Would fail if incomplete player scoring were summed as zero or discarded."""
    rows = _player_game_rows()
    rows.loc[
        rows["game_id"].eq("0028300001")
        & rows["franchise_id"].eq("BOS")
        & rows.index.to_series().eq(0),
        "pts",
    ] = None
    validated = player_games_build.cross_validate_coverage(
        player_games_build.aggregate_team_games(rows),
        *_coverage_references(),
        seasons=(1984, 2020),
    )

    assert player_games_build.collect_discrepancies(validated)["points"] == {
        "0028300001|BOS": {
            "game_id": "0028300001",
            "franchise_id": "BOS",
            "player_total": None,
            "published_total": 108,
            "season": 1984,
        }
    }


@pytest.mark.parametrize(
    ("total", "expected_overtime", "expected_residual"),
    [
        (240, 0, 0),
        (241, 0, 1),
        (263, 1, 2),
        (265, 1, 0),
        (290, 2, 0),
    ],
)
def test_minute_inference_uses_nearest_explicit_candidate(
    total, expected_overtime, expected_residual,
):
    """Would fail if minute inference used rounding or a non-deterministic tie path."""
    assert player_games_build._infer_overtime(total) == (
        expected_overtime,
        expected_residual,
    )


def test_null_minutes_become_a_canonical_discrepancy():
    """Would fail if incomplete minutes were summed as zero or discarded."""
    rows = _player_game_rows()
    rows.loc[0, "min"] = None
    validated = player_games_build.cross_validate_coverage(
        player_games_build.aggregate_team_games(rows),
        *_coverage_references(),
        seasons=(1984, 2020),
    )

    assert player_games_build.collect_discrepancies(validated)["minutes"][
        "0028300001|BOS"
    ] == {
        "game_id": "0028300001",
        "franchise_id": "BOS",
        "player_total": None,
        "opponent_total": 240,
        "inferred_overtime_periods": None,
        "published_overtime_periods": None,
        "reason": "player minute total is incomplete",
        "season": 1984,
    }


def test_277_minute_residual_becomes_a_discrepancy():
    """Would fail if a residual beyond five minutes were considered normal."""
    observed = player_games_build.collect_discrepancies(
        _regular_discrepancy_frame(277, 277)
    )

    assert observed["minutes"]["0028300001|BOS"] == {
        "game_id": "0028300001",
        "franchise_id": "BOS",
        "player_total": 277,
        "opponent_total": 277,
        "inferred_overtime_periods": 1,
        "published_overtime_periods": None,
        "reason": "minute total residual 12 exceeds tolerance 5",
        "season": 1984,
    }


def test_opposing_teams_inferring_different_overtime_counts_are_discrepancies():
    """Would fail if overtime inference agreement were checked team-by-team."""
    minutes = player_games_build.collect_discrepancies(
        _regular_discrepancy_frame(240, 265)
    )["minutes"]

    assert list(minutes) == ["0028300001|BOS", "0028300001|CHI"]
    assert {entry["inferred_overtime_periods"] for entry in minutes.values()} == {0, 1}
    assert all(
        entry["reason"] == "opposing teams infer different overtime periods"
        for entry in minutes.values()
    )


def test_minute_total_beyond_six_overtimes_becomes_a_discrepancy():
    """Would fail if inference silently extended beyond the declared ceiling."""
    minutes = player_games_build.collect_discrepancies(
        _regular_discrepancy_frame(400, 400)
    )["minutes"]

    assert minutes["0028300001|BOS"]["inferred_overtime_periods"] == 6
    assert minutes["0028300001|BOS"]["reason"] == (
        "minute total exceeds six-overtime ceiling"
    )


def test_postseason_inferred_and_published_overtime_disagreement_is_a_discrepancy():
    """Would fail if postseason published overtime evidence were ignored."""
    validated = _validated_coverage()
    validated = validated.loc[validated["game_id"].eq("0048300001")].copy()
    validated["published_overtime_periods"] = 1
    minutes = player_games_build.collect_discrepancies(validated)["minutes"]

    assert list(minutes) == ["0048300001|BOS", "0048300001|CHI"]
    assert all(
        entry["reason"] == "inferred/published overtime periods disagree"
        for entry in minutes.values()
    )


def test_minute_discrepancy_accepts_exact_approved_exception():
    """Would fail if a reviewed minute discrepancy could never clear validation."""
    observed = player_games_build.collect_discrepancies(
        _regular_discrepancy_frame(277, 277)
    )

    player_games_build.validate_exceptions(observed, _approved(observed))


def test_minute_discrepancy_rejects_value_drift_and_stale_exception():
    """Would fail if minute approvals were not exact, bidirectional equality."""
    observed = player_games_build.collect_discrepancies(
        _regular_discrepancy_frame(277, 277)
    )
    approved = _approved(observed)
    approved["minutes"]["0028300001|BOS"]["opponent_total"] = 276

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match=r"drift.*0028300001\|BOS.*277.*276",
    ):
        player_games_build.validate_exceptions(observed, approved)

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match=r"stale.*0028300001\|CHI",
    ):
        player_games_build.validate_exceptions(
            _ledger(minutes={"0028300001|BOS": observed["minutes"]["0028300001|BOS"]}),
            approved,
        )


def test_audit_source_assembles_offline_structured_evidence(monkeypatch, tmp_path):
    """Would fail if Task 5 had to reread source data to render review evidence."""
    _use_tiny_scope(monkeypatch)
    _publish_scope(tmp_path)
    _use_empty_exception_ledgers(monkeypatch, tmp_path)
    parquet_dir = tmp_path / "parquet"
    parquet_dir.mkdir()
    regular_games = []
    regular_team_games = []
    postseason_games = []
    for season in (1984, 1985):
        year = season - 1
        game_date = pd.Timestamp(f"{year}-10-28").date()
        regular_id = f"002{str(year)[-2:]}00001"
        playoff_id = f"004{str(year)[-2:]}00001"
        regular_games.append({
            "game_id": regular_id,
            "season": season,
            "game_date": game_date,
            "home_franchise_id": "BOS",
            "away_franchise_id": "CHI",
            "home_pts": 2,
            "away_pts": 2,
        })
        regular_team_games.extend([
            {
                "game_id": regular_id, "season": season,
                "game_date": game_date, "franchise_id": "BOS",
                "opponent_franchise_id": "CHI", "is_home": True,
                "pts": 2,
            },
            {
                "game_id": regular_id, "season": season,
                "game_date": game_date, "franchise_id": "CHI",
                "opponent_franchise_id": "BOS", "is_home": False,
                "pts": 2,
            },
        ])
        postseason_games.append({
            "game_id": f"bref-{year}1028BOS",
            "season": season,
            "game_date": game_date,
            "game_type": "playoff",
            "home_franchise_id": "BOS",
            "away_franchise_id": "CHI",
            "home_pts": 2,
            "away_pts": 2,
            "overtime_periods": 0,
        })
        assert playoff_id != postseason_games[-1]["game_id"]
    pd.DataFrame(regular_games).to_parquet(
        parquet_dir / "games.parquet", index=False
    )
    pd.DataFrame(regular_team_games).to_parquet(
        parquet_dir / "team_games.parquet", index=False
    )
    pd.DataFrame(postseason_games).to_parquet(
        parquet_dir / "playoff_games.parquet", index=False
    )

    audit = player_games_build.audit_source(tmp_path)

    assert list(audit) == [
        "player_games",
        "team_aggregates",
        "validated_team_games",
        "discrepancies",
        "approved_exceptions",
        "matchup_repairs",
        "designation_repairs",
    ]
    assert len(audit["player_games"]) == 8
    assert len(audit["team_aggregates"]) == 8
    assert len(audit["validated_team_games"]) == 8
    assert len(audit["discrepancies"]["minutes"]) == 8
    assert audit["discrepancies"]["points"] == {}
    assert audit["approved_exceptions"] == _ledger()
    assert audit["matchup_repairs"] == {}
    assert audit["designation_repairs"] == {}


def test_audit_source_returns_exact_matchup_approval_and_consensus_evidence(
    monkeypatch, tmp_path,
):
    """Would fail if audit lost the exact applied approval or its live proof."""
    _raw_root, _references, _ledger, _matchup_ledger = (
        _seed_matchup_build_inputs(monkeypatch, tmp_path)
    )

    audit = player_games_build.audit_source(tmp_path)

    assert audit["matchup_repairs"] == {
        MATCHUP_FIXTURE_KEY: {
            "approval": MATCHUP_FIXTURE_ENTRY,
            "consensus": {
                "game_date": "1983-10-28",
                "home_abbreviation": "BOS",
                "away_abbreviation": "CHI",
                "raw_team_identities": 2,
                "resolved_franchises": 2,
                "malformed_rows": 1,
                "valid_rows": 2,
                "valid_team_identities": 2,
                "valid_home_identities": 1,
                "valid_away_identities": 1,
            },
        }
    }


def _expected_designation_evidence():
    return {
        DESIGNATION_FIXTURE_KEY: {
            "approval": DESIGNATION_FIXTURE_ENTRY,
            "raw": {
                "game_date": "1983-10-28",
                "rows": 2,
                "raw_team_identities": 2,
                "resolved_franchises": 2,
                "valid_xor_rows": 2,
                "malformed_rows": 0,
                "unordered_matchup_pairs": 1,
                "home_identities_before": 0,
                "away_identities_before": 2,
                "home_identities_after": 1,
                "away_identities_after": 1,
                "teams": DESIGNATION_FIXTURE_ENTRY["teams"],
            },
            "reference": {
                "games_rows": 1,
                "team_games_rows": 2,
                "postseason_rows": 0,
                "game_date": "1983-10-28",
                "home_franchise_id": "BOS",
                "away_franchise_id": "CHI",
                "neutral_site": True,
                "neutral_site_source": "hoopr",
                "venue_name": "Fixture Arena",
                "venue_city": "Fixture City",
                "reciprocal_team_games": True,
                "normalized_home_identities": 1,
                "normalized_away_identities": 1,
            },
        }
    }


def test_audit_source_returns_exact_designation_raw_and_reference_evidence(
    monkeypatch, tmp_path,
):
    """Would fail if a neutral designation lost either proof layer."""
    _seed_designation_build_inputs(monkeypatch, tmp_path)

    audit = player_games_build.audit_source(tmp_path)

    assert audit["designation_repairs"] == _expected_designation_evidence()


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_game",
        "ambiguous_game",
        "non_neutral",
        "wrong_source",
        "wrong_sides",
        "nonreciprocal_team_games",
        "postseason_collision",
    ],
)
def test_designation_reference_proof_fails_closed(
    monkeypatch, tmp_path, mutation,
):
    """Would fail if mutable or ambiguous reference evidence were accepted."""
    _raw, references, *_rest = _seed_designation_build_inputs(
        monkeypatch, tmp_path
    )
    games_path = references / "games.parquet"
    team_games_path = references / "team_games.parquet"
    postseason_path = references / "playoff_games.parquet"
    games = pd.read_parquet(games_path)
    team_games = pd.read_parquet(team_games_path)
    postseason = pd.read_parquet(postseason_path)
    if mutation == "missing_game":
        games = games.loc[~games.game_id.eq("0028300001")]
    elif mutation == "ambiguous_game":
        games = pd.concat([games, games], ignore_index=True)
    elif mutation == "non_neutral":
        games.loc[games.game_id.eq("0028300001"), "neutral_site"] = False
    elif mutation == "wrong_source":
        games.loc[games.game_id.eq("0028300001"), "neutral_site_source"] = "manual"
    elif mutation == "wrong_sides":
        games.loc[games.game_id.eq("0028300001"), [
            "home_franchise_id", "away_franchise_id",
        ]] = ["CHI", "BOS"]
    elif mutation == "nonreciprocal_team_games":
        team_games.loc[
            team_games.franchise_id.eq("CHI"), "opponent_franchise_id"
        ] = "CHI"
    else:
        collision = postseason.iloc[[0]].copy()
        collision.loc[:, "game_id"] = "0028300001"
        postseason = pd.concat([postseason, collision], ignore_index=True)
    games.to_parquet(games_path, index=False)
    team_games.to_parquet(team_games_path, index=False)
    postseason.to_parquet(postseason_path, index=False)

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match="designation reference",
    ):
        player_games_build.audit_source(tmp_path)


@pytest.mark.parametrize(
    ("table", "value"),
    [
        ("neutral_site", 1),
        ("neutral_site", "true"),
        ("neutral_site", None),
        ("is_home", 0),
        ("is_home", ""),
        ("is_home", None),
    ],
    ids=[
        "neutral-integer",
        "neutral-string",
        "neutral-null",
        "home-integer",
        "home-string",
        "home-null",
    ],
)
def test_designation_reference_requires_exact_non_null_booleans(
    monkeypatch, tmp_path, table, value,
):
    """Would fail if truthiness could replace exact reference booleans."""
    _raw, references, *_rest = _seed_designation_build_inputs(
        monkeypatch, tmp_path
    )
    audit = player_games_build.audit_source(tmp_path)
    games = pd.read_parquet(references / "games.parquet")
    team_games = pd.read_parquet(references / "team_games.parquet")
    postseason = pd.read_parquet(references / "playoff_games.parquet")
    if table == "neutral_site":
        games["neutral_site"] = games["neutral_site"].astype(object)
        games.loc[games.game_id.eq("0028300001"), "neutral_site"] = value
    else:
        team_games["is_home"] = team_games["is_home"].astype(object)
        team_games.loc[
            team_games.game_id.eq("0028300001")
            & team_games.franchise_id.eq("CHI"),
            "is_home",
        ] = value

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match="exact non-null boolean",
    ):
        player_games_build._validate_designation_references(
            audit["player_games"],
            audit["designation_repairs"],
            games,
            team_games,
            postseason,
        )


def test_audit_rejects_playoff_mutation_after_designation_reference_proof(
    monkeypatch, tmp_path,
):
    """Would fail if audit evidence and playoff bytes could diverge."""
    _raw, references, *_rest = _seed_designation_build_inputs(
        monkeypatch, tmp_path
    )
    real_validate = player_games_build._validate_designation_references

    def mutate_after_reference_proof(*args, **kwargs):
        result = real_validate(*args, **kwargs)
        playoff_path = references / "playoff_games.parquet"
        playoff_path.write_bytes(playoff_path.read_bytes() + b" ")
        return result

    monkeypatch.setattr(
        player_games_build,
        "_validate_designation_references",
        mutate_after_reference_proof,
    )

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match="playoff_games.parquet changed",
    ):
        player_games_build.audit_source(tmp_path)


@pytest.mark.parametrize(
    ("section", "entry"),
    [
        ("points", _points_exception()),
        ("minutes", _minutes_exception()),
    ],
)
def test_publication_scope_rejects_stale_nonempty_ledger(
    monkeypatch,
    tmp_path,
    section,
    entry,
):
    """Would fail if a stale reviewed exception could pass a clean build."""
    raw_root, references, ledger = _seed_build_inputs(monkeypatch, tmp_path)
    document = _ledger()
    key = f"{entry['game_id']}|{entry['franchise_id']}"
    document[section][key] = entry
    _save_ledger(ledger, document)
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match="stale.*" + re.escape(key),
    ):
        player_games_build.build_from_roots(raw_root, references, stage)


def test_manifest_binds_only_trusted_publication_scope(tmp_path, monkeypatch):
    """Would fail if the manifest still bound quarantined source seasons."""
    stage, manifest = _build_scoped_publication(
        tmp_path,
        monkeypatch,
        source_seasons=(2011, 2012, 2013),
        publication_seasons=(2012, 2013),
    )

    assert manifest["scope"]["ending_seasons"] == [2012, 2013]
    assert set(manifest["partitions"]) == {"2012", "2013"}
    assert len(manifest["sources"]["player_logs"]) == 4
    assert {
        entry["identity"]["season"]
        for entry in manifest["sources"]["player_logs"]
    } == {2012, 2013}
    assert manifest["validation"]["cache_entries"] == 4
    assert manifest["validation"]["exceptions"] == {
        "minutes": 0,
        "points": 0,
    }
    assert manifest["validation"]["matchup_repairs"]["count"] == 0


def test_publication_layout_rejects_quarantined_partition(tmp_path, monkeypatch):
    """Would fail if the layout check still tolerated a quarantined season."""
    root, _manifest = _build_scoped_publication(
        tmp_path,
        monkeypatch,
        source_seasons=(2011, 2012),
        publication_seasons=(2012,),
    )
    (root / "season=2011").mkdir()
    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match="extra=.*season=2011",
    ):
        player_games_build.validate_publication(
            tmp_path / "raw/stats_nba/player_logs",
            root,
            tmp_path / "parquet",
        )


def test_validate_publication_rejects_quarantined_source_binding(
    tmp_path, monkeypatch,
):
    """Would fail if validation trusted a stored source list instead of
    recomputing exactly the publication-scope binding."""
    stage, _manifest = _build_scoped_publication(
        tmp_path,
        monkeypatch,
        source_seasons=(2011, 2012),
        publication_seasons=(2012,),
    )
    manifest_path = stage / "_manifest.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    quarantined = dict(document["sources"]["player_logs"][0])
    quarantined["body_path"] = "season=2011/regular.json"
    document["sources"]["player_logs"].append(quarantined)
    manifest_path.write_bytes(player_games_build._manifest_bytes(document))

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match="manifest source mismatch",
    ):
        player_games_build.validate_publication(
            tmp_path / "raw/stats_nba/player_logs",
            stage,
            tmp_path / "parquet",
        )


def test_build_from_roots_writes_exact_deterministic_manifest(monkeypatch, tmp_path):
    """Would fail if manifest claims depended on staging paths or write order."""
    raw_root, references, _ledger_path = _seed_build_inputs(monkeypatch, tmp_path)
    stages = [tmp_path / "first-stage", tmp_path / "second-stage"]
    for stage in stages:
        stage.mkdir()
        player_games_build.build_from_roots(raw_root, references, stage)

    first = (stages[0] / "_manifest.json").read_bytes()
    second = (stages[1] / "_manifest.json").read_bytes()
    manifest = json.loads(first)

    assert first == second
    assert first.endswith(b"\n")
    assert set(manifest) == {
        "schema_version",
        "scope",
        "schema",
        "versions",
        "sources",
        "references",
        "exception_ledger",
        "partitions",
        "validation",
    }
    assert manifest["scope"] == {
        "ending_seasons": [1984, 1985],
        "game_types": ["playoff", "regular"],
    }
    assert list(manifest["references"]) == [
        "build_manifest.json",
        "games.parquet",
        "playoff_games.parquet",
        "playoff_games_manifest.json",
        "team_games.parquet",
    ]
    assert len(manifest["sources"]["player_logs"]) == 4
    assert set(manifest["sources"]) == {
        "franchise_reference", "player_logs"
    }
    assert set(manifest["partitions"]) == {"1984", "1985"}
    for season, entry in manifest["partitions"].items():
        assert set(entry) == {
            "bytes", "game_counts", "path", "players", "row_sha256",
            "rows", "sha256",
        }
        assert entry["path"] == f"season={season}/player_games.parquet"
        assert entry["rows"] == 4
        assert entry["players"] == 2
        assert entry["game_counts"] == {"playoff": 1, "regular": 1}
        assert len(entry["sha256"]) == len(entry["row_sha256"]) == 64
    assert manifest["validation"] == {
        "cache_entries": 4,
        "coverage": {
            "postseason_team_games": 4,
            "regular_team_games": 4,
            "status": "passed",
            "team_games": 8,
        },
        "exceptions": {"minutes": 0, "points": 0},
        "matchup_repairs": {
            "count": 0,
            "sha256": "ca3d163bab055381827226140568f3bef7eaac187cebd76878e0b63e9e442356",
        },
        "designation_repairs": {
            "count": 0,
            "sha256": "ca3d163bab055381827226140568f3bef7eaac187cebd76878e0b63e9e442356",
        },
        "games": 4,
        "players": 2,
        "rows": 8,
    }
    rendered = first.decode("utf-8")
    assert "/home/" not in rendered
    assert str(tmp_path) not in rendered
    assert "fetched_at" not in rendered
    assert "NaN" not in rendered


def test_build_binds_all_ledgers_and_the_exact_applied_matchup_digest(
    monkeypatch, tmp_path,
):
    """Would fail if manifest bytes and normalization semantics could diverge."""
    raw_root, references, points_ledger, matchup_ledger = (
        _seed_matchup_build_inputs(monkeypatch, tmp_path)
    )
    stage = tmp_path / "stage"
    stage.mkdir()

    manifest = player_games_build.build_from_roots(
        raw_root, references, stage
    )

    assert manifest["exception_ledger"] == {
        "points_minutes": {
            "path": "src/nba_data/reference/player_game_validation_exceptions.json",
            "sha256": hashlib.sha256(points_ledger.read_bytes()).hexdigest(),
            "bytes": len(points_ledger.read_bytes()),
        },
        "matchups": {
            "path": "src/nba_data/reference/player_game_matchup_exceptions.json",
            "sha256": "3c5ef85bbf6b284a5972fca3ae84d61a7e1f0cbc10f4bf339befe519bb5701ac",
            "bytes": 585,
        },
        "designations": {
            "path": "src/nba_data/reference/player_game_designation_exceptions.json",
            "sha256": hashlib.sha256(
                EMPTY_DESIGNATION_LEDGER_BYTES
            ).hexdigest(),
            "bytes": len(EMPTY_DESIGNATION_LEDGER_BYTES),
        },
    }
    assert matchup_ledger.read_bytes() == MATCHUP_FIXTURE_LEDGER_BYTES
    assert manifest["validation"]["matchup_repairs"] == {
        "count": 1,
        "sha256": "d78415f5cd5ceb85af4512a68474ca15b101f7d01fe92805e6289dd77cb56ce7",
    }
    assert manifest["validation"]["designation_repairs"] == {
        "count": 0,
        "sha256": "ca3d163bab055381827226140568f3bef7eaac187cebd76878e0b63e9e442356",
    }


def test_build_binds_designation_ledger_and_exact_two_layer_proof(
    monkeypatch, tmp_path,
):
    """Would fail if designation bytes and proof semantics could diverge."""
    raw_root, references, _points, _matchups, designation_ledger = (
        _seed_designation_build_inputs(monkeypatch, tmp_path)
    )
    stage = tmp_path / "stage"
    stage.mkdir()

    manifest = player_games_build.build_from_roots(raw_root, references, stage)

    expected_evidence = _expected_designation_evidence()
    assert manifest["exception_ledger"]["designations"] == {
        "path": "src/nba_data/reference/player_game_designation_exceptions.json",
        "sha256": hashlib.sha256(designation_ledger.read_bytes()).hexdigest(),
        "bytes": len(designation_ledger.read_bytes()),
    }
    assert manifest["validation"]["designation_repairs"] == {
        "count": 1,
        "sha256": hashlib.sha256(
            player_games_build._manifest_bytes(expected_evidence)
        ).hexdigest(),
    }


def test_build_applies_only_release_scoped_repairs(monkeypatch, tmp_path):
    """Would fail if repair application ignored PUBLICATION_SEASONS scoping
    and applied every approved ledger entry regardless of season."""
    raw_root, references, _ledger, matchup_ledger, designation_ledger = (
        _seed_release_scoped_repair_inputs(monkeypatch, tmp_path)
    )
    stage = tmp_path / "stage"
    stage.mkdir()

    manifest = player_games_build.build_from_roots(raw_root, references, stage)

    assert manifest["validation"]["matchup_repairs"]["count"] == 0
    assert manifest["validation"]["designation_repairs"]["count"] == 1
    assert manifest["exception_ledger"]["matchups"] == {
        "path": "src/nba_data/reference/player_game_matchup_exceptions.json",
        "sha256": hashlib.sha256(matchup_ledger.read_bytes()).hexdigest(),
        "bytes": len(matchup_ledger.read_bytes()),
    }
    assert manifest["exception_ledger"]["designations"] == {
        "path": "src/nba_data/reference/player_game_designation_exceptions.json",
        "sha256": hashlib.sha256(designation_ledger.read_bytes()).hexdigest(),
        "bytes": len(designation_ledger.read_bytes()),
    }

    matchup_ledger.write_bytes(matchup_ledger.read_bytes() + b" ")
    with pytest.raises(
        player_games_build.PlayerGamesBuildError, match="exception ledger"
    ):
        player_games_build.validate_publication(raw_root, stage, references)
    matchup_ledger.write_bytes(matchup_ledger.read_bytes()[:-1])

    designation_ledger.write_bytes(designation_ledger.read_bytes() + b" ")
    with pytest.raises(
        player_games_build.PlayerGamesBuildError, match="exception ledger"
    ):
        player_games_build.validate_publication(raw_root, stage, references)


@pytest.mark.parametrize(
    "mutation", ["designation", "games", "team_games", "playoff_games"]
)
def test_build_rejects_designation_or_reference_mutation_during_recomputation(
    monkeypatch, tmp_path, mutation,
):
    """Would fail if proof used bytes different from the manifest binding."""
    raw_root, references, _points, _matchups, designation_ledger = (
        _seed_designation_build_inputs(monkeypatch, tmp_path)
    )
    real_assemble = player_games_build._assemble_season_from_root
    mutated = False

    def mutate_after_assembly(*args, **kwargs):
        nonlocal mutated
        result = real_assemble(*args, **kwargs)
        if not mutated:
            if mutation == "designation":
                target = designation_ledger
            else:
                target = references / f"{mutation}.parquet"
            target.write_bytes(target.read_bytes() + b" ")
            mutated = True
        return result

    monkeypatch.setattr(
        player_games_build,
        "_assemble_season_from_root",
        mutate_after_assembly,
    )
    stage = tmp_path / "stage"
    stage.mkdir()

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match=(
            "designation exception ledger changed"
            if mutation == "designation"
            else f"{mutation}.parquet changed"
        ),
    ):
        player_games_build.build_from_roots(raw_root, references, stage)


def test_build_rejects_matchup_ledger_mutation_during_recomputation(
    monkeypatch, tmp_path,
):
    """Would fail if pinned bytes changed after their mapping governed rows."""
    raw_root, references, _points_ledger, matchup_ledger = (
        _seed_matchup_build_inputs(monkeypatch, tmp_path)
    )
    real_assemble = player_games_build._assemble_season_from_root
    mutated = False

    def mutate_after_assembly(*args, **kwargs):
        nonlocal mutated
        result = real_assemble(*args, **kwargs)
        if not mutated:
            matchup_ledger.write_bytes(matchup_ledger.read_bytes() + b" ")
            mutated = True
        return result

    monkeypatch.setattr(
        player_games_build,
        "_assemble_season_from_root",
        mutate_after_assembly,
    )
    stage = tmp_path / "stage"
    stage.mkdir()

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match="matchup exception ledger changed",
    ):
        player_games_build.build_from_roots(raw_root, references, stage)


def test_build_uses_live_pinned_franchise_bytes_after_cache_warmup(
    monkeypatch, tmp_path
):
    """Would fail if cached franchise semantics were bound to newer CSV bytes."""
    raw_root, references, ledger = _seed_build_inputs(monkeypatch, tmp_path)
    franchises.load_reference.cache_clear()
    assert franchises.resolve("Boston Celtics", 1984) == "BOS"
    fixture = tmp_path / "franchises.csv"
    fixture.write_bytes(
        player_games_build.FRANCHISE_REFERENCE_PATH.read_bytes().replace(
            b"Boston Celtics,BOS,,", b"Boston Celtics,BOS_LIVE,,"
        )
    )
    _replace_fixture_franchise(references, ledger, "BOS", "BOS_LIVE")
    monkeypatch.setattr(
        player_games_build, "FRANCHISE_REFERENCE_PATH", fixture
    )
    stage = tmp_path / "stage"
    stage.mkdir()

    player_games_build.build_from_roots(raw_root, references, stage)

    table = pq.read_table(stage / "season=1984/player_games.parquet")
    assert set(table.column("franchise_id").to_pylist()) == {
        "BOS_LIVE", "CHI"
    }
    manifest = json.loads((stage / "_manifest.json").read_text())
    assert manifest["sources"]["franchise_reference"]["sha256"] == (
        hashlib.sha256(fixture.read_bytes()).hexdigest()
    )


def test_build_and_validation_use_live_only_alias_after_cache_warmup(
    monkeypatch, tmp_path
):
    """Would fail if normalization consulted stale aliases before pinned bytes."""
    raw_root, references, ledger = _seed_build_inputs(monkeypatch, tmp_path)
    live_alias = "Boston Live Alias"
    franchises.load_reference.cache_clear()
    with pytest.raises(franchises.UnknownTeamError):
        franchises.resolve(live_alias, 1984)

    fixture = tmp_path / "franchises.csv"
    fixture.write_bytes(
        player_games_build.FRANCHISE_REFERENCE_PATH.read_bytes()
        + f"{live_alias},BOS_LIVE,,\n".encode()
    )
    monkeypatch.setattr(franchises, "REFERENCE", fixture)
    monkeypatch.setattr(
        player_games_build, "FRANCHISE_REFERENCE_PATH", fixture
    )
    for season in player_games_build.PUBLICATION_SEASONS:
        for game_type in ("regular", "playoff"):
            document = json.loads(_payload(season, game_type))
            result_set = document["resultSets"][0]
            team_name_index = result_set["headers"].index("TEAM_NAME")
            min_index = result_set["headers"].index("MIN")
            for row in result_set["rowSet"]:
                if row[team_name_index] == "Boston Celtics":
                    row[team_name_index] = live_alias
                row[min_index] = 240
            payload = json.dumps(document).encode()
            cache.publish_bytes(
                player_logs.cache_path(tmp_path, season, game_type),
                payload,
                _metadata(season, game_type),
                player_logs.validate_payload,
            )
    _replace_fixture_franchise(references, ledger, "BOS", "BOS_LIVE")
    stage = tmp_path / "stage"
    stage.mkdir()

    manifest = player_games_build.build_from_roots(
        raw_root, references, stage
    )

    assert player_games_build.validate_publication(
        raw_root, stage, references
    ) == manifest
    table = pq.read_table(stage / "season=1984/player_games.parquet")
    assert set(table.column("franchise_id").to_pylist()) == {
        "BOS_LIVE", "CHI"
    }


def test_build_rejects_franchise_reference_mutation_during_recomputation(
    monkeypatch, tmp_path
):
    """Would fail if semantics and pinned bytes came from different snapshots."""
    raw_root, references, _ledger_path = _seed_build_inputs(monkeypatch, tmp_path)
    fixture = tmp_path / "franchises.csv"
    fixture.write_bytes(player_games_build.FRANCHISE_REFERENCE_PATH.read_bytes())
    monkeypatch.setattr(
        player_games_build, "FRANCHISE_REFERENCE_PATH", fixture
    )
    real_assemble = player_games_build._assemble_season_from_root
    mutated = False

    def mutate_after_assembly(*args, **kwargs):
        nonlocal mutated
        result = real_assemble(*args, **kwargs)
        if not mutated:
            fixture.write_bytes(fixture.read_bytes() + b"\n")
            mutated = True
        return result

    monkeypatch.setattr(
        player_games_build,
        "_assemble_season_from_root",
        mutate_after_assembly,
    )
    stage = tmp_path / "stage"
    stage.mkdir()

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match="franchise reference changed",
    ):
        player_games_build.build_from_roots(raw_root, references, stage)


def test_manifest_versions_ignore_runtime_discovery(monkeypatch):
    """Would fail if host package or Python patch versions entered a manifest."""
    monkeypatch.setattr(pd, "__version__", "host-pandas")
    monkeypatch.setattr(pa, "__version__", "host-pyarrow")
    monkeypatch.setattr(
        importlib.metadata, "version", lambda _name: "host-package"
    )

    assert player_games_build._versions() == {
        "nba_api": "1.11.4",
        "pandas": "3.0.5",
        "pyarrow": "25.0.0",
        "python": ">=3.12",
    }


def test_manifest_source_identity_uses_exact_stable_allowlist(
    monkeypatch, tmp_path
):
    """Would fail if future timestamps, paths, or host fields leaked in."""
    raw_root, references, _ledger_path = _seed_build_inputs(monkeypatch, tmp_path)
    sidecar = raw_root / "season=1984/regular.json.meta.json"
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    metadata.update({
        "future_timestamp": "2099-01-01T00:00:00Z",
        "host_path": "/home/reviewer/cache",
        "hostname": "review-host",
    })
    sidecar.write_text(
        json.dumps(metadata, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    stage = tmp_path / "stage"
    stage.mkdir()

    player_games_build.build_from_roots(raw_root, references, stage)

    manifest = json.loads((stage / "_manifest.json").read_text())
    source = next(
        entry for entry in manifest["sources"]["player_logs"]
        if entry["body_path"] == "season=1984/regular.json"
    )
    assert source["identity"] == _metadata(1984, "regular")
    rendered = json.dumps(manifest, sort_keys=True)
    assert "future_timestamp" not in rendered
    assert "/home/reviewer" not in rendered
    assert "review-host" not in rendered


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("manifest", "manifest"),
        ("raw_sidecar", "source"),
        ("reference", "reference"),
        ("ledger", "exception ledger"),
        ("franchise", "franchise"),
        ("partition_bytes", "partition"),
    ],
)
def test_validate_publication_recomputes_live_inputs(
    monkeypatch, tmp_path, target, expected
):
    """Would fail if validation trusted a stored checksum or count claim."""
    raw_root, references, ledger = _seed_build_inputs(monkeypatch, tmp_path)
    franchise = tmp_path / "franchises.csv"
    franchise.write_bytes(
        player_games_build.FRANCHISE_REFERENCE_PATH.read_bytes()
    )
    monkeypatch.setattr(
        player_games_build, "FRANCHISE_REFERENCE_PATH", franchise
    )
    stage = tmp_path / "stage"
    stage.mkdir()
    player_games_build.build_from_roots(raw_root, references, stage)

    if target == "manifest":
        path = stage / "_manifest.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["validation"]["rows"] += 1
        path.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n")
    elif target == "raw_sidecar":
        path = raw_root / "season=1984/regular.json.meta.json"
        path.write_bytes(path.read_bytes() + b" ")
    elif target == "reference":
        (references / "build_manifest.json").write_bytes(
            (references / "build_manifest.json").read_bytes() + b" "
        )
    elif target == "ledger":
        ledger.write_bytes(ledger.read_bytes() + b" ")
    elif target == "franchise":
        franchise.write_bytes(franchise.read_bytes() + b"\n")
    else:
        partition = stage / "season=1984/player_games.parquet"
        partition.write_bytes(partition.read_bytes() + b"drift")

    with pytest.raises(player_games_build.PlayerGamesBuildError, match=expected):
        player_games_build.validate_publication(raw_root, stage, references)


def test_validate_publication_rejects_source_identity_lie(monkeypatch, tmp_path):
    """Would fail if source metadata identity were not independently derived."""
    raw_root, references, _ledger_path = _seed_build_inputs(monkeypatch, tmp_path)
    stage = tmp_path / "stage"
    stage.mkdir()
    player_games_build.build_from_roots(raw_root, references, stage)
    path = stage / "_manifest.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["sources"]["player_logs"][0]["identity"]["provider"] = "lie"
    path.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n")

    with pytest.raises(player_games_build.PlayerGamesBuildError, match="source"):
        player_games_build.validate_publication(raw_root, stage, references)


@pytest.mark.parametrize(
    "mutation",
    ["ledger_binding", "repair_count", "repair_digest"],
)
def test_validate_publication_recomputes_matchup_ledger_and_repair_evidence(
    monkeypatch, tmp_path, mutation,
):
    """Would fail if independent validation trusted stored matchup claims."""
    raw_root, references, _points_ledger, _matchup_ledger = (
        _seed_matchup_build_inputs(monkeypatch, tmp_path)
    )
    stage = tmp_path / "stage"
    stage.mkdir()
    player_games_build.build_from_roots(raw_root, references, stage)
    manifest_path = stage / "_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mutation == "ledger_binding":
        manifest["exception_ledger"]["matchups"]["sha256"] = "0" * 64
    elif mutation == "repair_count":
        manifest["validation"]["matchup_repairs"]["count"] = 2
    else:
        manifest["validation"]["matchup_repairs"]["sha256"] = "f" * 64
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(player_games_build.PlayerGamesBuildError):
        player_games_build.validate_publication(raw_root, stage, references)


@pytest.mark.parametrize(
    "mutation",
    ["ledger_binding", "repair_count", "repair_digest"],
)
def test_validate_publication_recomputes_designation_ledger_and_proof(
    monkeypatch, tmp_path, mutation,
):
    """Would fail if validation trusted stored neutral-designation claims."""
    raw_root, references, *_rest = _seed_designation_build_inputs(
        monkeypatch, tmp_path
    )
    stage = tmp_path / "stage"
    stage.mkdir()
    player_games_build.build_from_roots(raw_root, references, stage)
    manifest_path = stage / "_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mutation == "ledger_binding":
        manifest["exception_ledger"]["designations"]["sha256"] = "0" * 64
    elif mutation == "repair_count":
        manifest["validation"]["designation_repairs"]["count"] = 2
    else:
        manifest["validation"]["designation_repairs"]["sha256"] = "f" * 64
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(player_games_build.PlayerGamesBuildError):
        player_games_build.validate_publication(raw_root, stage, references)


def test_validate_publication_rejects_live_matchup_ledger_byte_drift(
    monkeypatch, tmp_path,
):
    """Would fail if semantically equivalent but unbound ledger bytes passed."""
    raw_root, references, _points_ledger, matchup_ledger = (
        _seed_matchup_build_inputs(monkeypatch, tmp_path)
    )
    stage = tmp_path / "stage"
    stage.mkdir()
    player_games_build.build_from_roots(raw_root, references, stage)
    matchup_ledger.write_bytes(matchup_ledger.read_bytes() + b" ")

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match="exception ledger",
    ):
        player_games_build.validate_publication(raw_root, stage, references)


def test_validate_publication_rejects_noncanonical_manifest_bytes(
    monkeypatch, tmp_path
):
    """Would fail if deterministic serialization were enforced only on write."""
    raw_root, references, _ledger_path = _seed_build_inputs(monkeypatch, tmp_path)
    stage = tmp_path / "stage"
    stage.mkdir()
    player_games_build.build_from_roots(raw_root, references, stage)
    manifest_path = stage / "_manifest.json"
    manifest_path.write_bytes(manifest_path.read_bytes() + b" ")

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match="canonical",
    ):
        player_games_build.validate_publication(raw_root, stage, references)


def test_validate_publication_rejects_logical_row_and_schema_drift(
    monkeypatch, tmp_path
):
    """Would fail if a self-consistent manifest could bless different rows."""
    raw_root, references, _ledger_path = _seed_build_inputs(monkeypatch, tmp_path)
    stage = tmp_path / "stage"
    stage.mkdir()
    player_games_build.build_from_roots(raw_root, references, stage)
    partition = stage / "season=1984/player_games.parquet"
    table = pq.read_table(partition)
    changed = table.set_column(
        table.schema.get_field_index("pts"),
        "pts",
        pa.array([99] + table.column("pts").to_pylist()[1:], type=pa.int64()),
    )
    pq.write_table(changed, partition, compression="zstd", use_dictionary=False)
    manifest_path = stage / "_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = manifest["partitions"]["1984"]
    entry["sha256"] = hashlib.sha256(partition.read_bytes()).hexdigest()
    entry["bytes"] = partition.stat().st_size
    entry["row_sha256"] = player_games_build._logical_row_sha256(changed)
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(player_games_build.PlayerGamesBuildError, match="rows"):
        player_games_build.validate_publication(raw_root, stage, references)

    pq.write_table(
        table.append_column("unexpected", pa.array([1] * table.num_rows)),
        partition,
        compression="zstd",
    )
    with pytest.raises(player_games_build.PlayerGamesBuildError, match="schema"):
        player_games_build.validate_publication(raw_root, stage, references)


@pytest.mark.parametrize("layout", ["missing", "extra", "symlink", "fifo"])
def test_validate_publication_requires_exact_regular_tree(
    monkeypatch, tmp_path, layout
):
    """Would fail if publication walking followed or ignored unsafe entries."""
    raw_root, references, _ledger_path = _seed_build_inputs(monkeypatch, tmp_path)
    stage = tmp_path / "stage"
    stage.mkdir()
    player_games_build.build_from_roots(raw_root, references, stage)
    if layout == "missing":
        (stage / "season=1985/player_games.parquet").unlink()
    elif layout == "extra":
        (stage / "unexpected.txt").write_text("unexpected")
    elif layout == "symlink":
        (stage / "unexpected-link").symlink_to(stage / "_manifest.json")
    else:
        os.mkfifo(stage / "unexpected-fifo")

    with pytest.raises(player_games_build.PlayerGamesBuildError):
        player_games_build.validate_publication(raw_root, stage, references)


def test_write_source_review_writes_only_canonical_candidates(
    monkeypatch, tmp_path
):
    """Would fail if source review claimed correctness or omitted absence limits."""
    _use_tiny_scope(monkeypatch)
    _publish_scope(tmp_path)
    _publish_references(tmp_path)
    _use_empty_exception_ledgers(monkeypatch, tmp_path)
    audit = player_games_build.audit_source(tmp_path)
    output = tmp_path / "review"

    player_games_build.write_source_review(output, audit)

    assert sorted(path.name for path in output.iterdir()) == [
        "player_game_validation_exceptions.json", "source-audit.md"
    ]
    assert all(path.is_file() and not path.is_symlink() for path in output.iterdir())
    candidates = json.loads(
        (output / "player_game_validation_exceptions.json").read_text()
    )
    assert set(candidates) == {"minutes", "points"}
    assert set(candidates["minutes"]) == set(audit["discrepancies"]["minutes"])
    for key, entry in candidates["minutes"].items():
        assert {k: v for k, v in entry.items() if k != "note"} == (
            audit["discrepancies"]["minutes"][key]
        )
        assert "raw source semantics are retained" in entry["note"]
        assert "correct" not in entry["note"].lower()
    markdown = (output / "source-audit.md").read_text(encoding="utf-8")
    for required in (
        "1984", "1985", "regular", "playoff", "Cache entries: 4",
        "Coverage status: passed", "Point candidate keys", "Minute candidate keys",
        "missing appearance does not prove roster membership, eligibility, injury, rest, or expected participation",
    ):
        assert required in markdown


def test_write_source_review_deterministically_enumerates_matchup_repairs(
    monkeypatch, tmp_path,
):
    """Would fail if source review omitted raw, expected, or consensus proof."""
    _raw_root, _references, _ledger, _matchup_ledger = (
        _seed_matchup_build_inputs(monkeypatch, tmp_path)
    )
    audit = player_games_build.audit_source(tmp_path)
    outputs = [tmp_path / "review-a", tmp_path / "review-b"]

    for output in outputs:
        player_games_build.write_source_review(output, audit)

    first = (outputs[0] / "source-audit.md").read_bytes()
    second = (outputs[1] / "source-audit.md").read_bytes()
    assert first == second
    assert (
        b"| 1984|regular|0028300001|2 | 1983-10-28 | CHI @ CHI | "
        b"BOS | CHI | 2 | 2 | 2 | 1 | 2 | 1 | 1 |\n"
    ) in first


def test_write_source_review_deterministically_enumerates_designations(
    monkeypatch, tmp_path,
):
    """Would fail if the designation table omitted raw or reference proof."""
    _seed_designation_build_inputs(monkeypatch, tmp_path)
    audit = player_games_build.audit_source(tmp_path)
    outputs = [tmp_path / "designation-a", tmp_path / "designation-b"]

    for output in outputs:
        player_games_build.write_source_review(output, audit)

    first = (outputs[0] / "source-audit.md").read_bytes()
    second = (outputs[1] / "source-audit.md").read_bytes()
    assert first == second
    assert (
        b"| 1984|regular|0028300001 | 1983-10-28 | "
        b"BOS: BOS @ CHI (1); CHI: CHI @ BOS (1) | BOS | CHI | hoopr | "
        b"Fixture Arena, Fixture City | 2 | 2 | 1 | 1 | 2 | 0 |\n"
    ) in first


@pytest.mark.parametrize("unsafe", ["nonempty", "symlink", "fifo"])
def test_write_source_review_refuses_unsafe_output(monkeypatch, tmp_path, unsafe):
    """Would fail if source review could overwrite or follow an unsafe path."""
    _use_tiny_scope(monkeypatch)
    _publish_scope(tmp_path)
    _publish_references(tmp_path)
    _use_empty_exception_ledgers(monkeypatch, tmp_path)
    audit = player_games_build.audit_source(tmp_path)
    output = tmp_path / "review"
    if unsafe == "nonempty":
        output.mkdir()
        (output / "keep.txt").write_text("keep")
    elif unsafe == "symlink":
        target = tmp_path / "elsewhere"
        target.mkdir()
        output.symlink_to(target, target_is_directory=True)
    else:
        os.mkfifo(output)

    with pytest.raises(player_games_build.PlayerGamesBuildError):
        player_games_build.write_source_review(output, audit)


def test_run_rereads_then_publishes_and_replaces(monkeypatch, tmp_path):
    """Would fail if run published before independent stage validation."""
    _seed_build_inputs(monkeypatch, tmp_path)
    calls = []
    real_validate = player_games_build.validate_publication

    def record_validate(raw_root, publication_dir, reference_dir):
        calls.append(Path(publication_dir))
        return real_validate(raw_root, publication_dir, reference_dir)

    monkeypatch.setattr(player_games_build, "validate_publication", record_validate)
    player_games_build.run(tmp_path)
    output = tmp_path / "parquet-large/player_games"
    first_manifest = (output / "_manifest.json").read_bytes()
    player_games_build.run(tmp_path)

    assert (output / "_manifest.json").read_bytes() == first_manifest
    assert calls[0] != output
    assert calls[1] == output
    assert calls[2] != output
    assert calls[3] == output


def test_run_reports_retained_rollback(monkeypatch, tmp_path):
    """Would fail if a cleanup failure silently discarded its recovery path."""
    _seed_build_inputs(monkeypatch, tmp_path)
    player_games_build.run(tmp_path)
    real_publish = publication.publish_tree

    def retain_rollback(stage_dir, output_dir, *, label):
        assert real_publish(stage_dir, output_dir, label=label) is None
        return output_dir.parent / ".player_games.rollback-retained"

    monkeypatch.setattr(publication, "publish_tree", retain_rollback)
    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match=r"rollback.*\.player_games\.rollback-retained",
    ):
        player_games_build.run(tmp_path)


def test_run_reports_retained_rollback_before_active_validation(
    monkeypatch, tmp_path
):
    """Would fail if active validation hid the operator recovery path."""
    manifest = {"validation": {"rows": 1}}
    output = tmp_path / "parquet-large/player_games"
    rollback = output.parent / ".player_games.rollback-retained"
    active_calls = []

    monkeypatch.setattr(
        player_games_build,
        "build_tree",
        lambda _data_dir, _stage_dir: manifest,
    )

    def validate(_raw_root, publication_dir, _reference_dir):
        if Path(publication_dir) == output:
            active_calls.append(Path(publication_dir))
            raise player_games_build.PlayerGamesBuildError(
                "active validation failed"
            )
        return manifest

    monkeypatch.setattr(player_games_build, "validate_publication", validate)
    monkeypatch.setattr(
        publication,
        "publish_tree",
        lambda *_args, **_kwargs: rollback,
    )

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match=str(rollback),
    ):
        player_games_build.run(tmp_path)

    assert active_calls == []


def _conforming_final_manifest(*, rows=8, games=4, players=2):
    """A manifest shaped exactly like the approved trusted-publication release:
    scope 2012-2026, 37 consumed source identities, 15 partitions, zero
    point/minute exceptions, zero matchup repairs, ten designation repairs."""
    identities = player_logs.cache_identities(player_games_build.PUBLICATION_SEASONS)
    return {
        "scope": {"ending_seasons": list(sorted(player_games_build.PUBLICATION_SEASONS))},
        "sources": {
            "player_logs": [
                {"season": season, "game_type": game_type}
                for season, game_type in identities
            ]
        },
        "partitions": {
            str(season): {} for season in player_games_build.PUBLICATION_SEASONS
        },
        "validation": {
            "rows": rows,
            "games": games,
            "players": players,
            "cache_entries": len(identities),
            "exceptions": {"minutes": 0, "points": 0},
            "matchup_repairs": {"count": 0},
            "designation_repairs": {"count": 10},
        },
    }


def test_render_final_audit_binds_manifest_hash_and_restore():
    """Would fail if final prose could claim an unverified manifest or restore,
    or omit the approved two-scope/quarantine evidence."""
    manifest = _conforming_final_manifest()
    payload = player_games_build._manifest_bytes(manifest)
    digest = hashlib.sha256(payload).hexdigest()
    snapshot_name = f"player-games-archive-2026-{digest[:12]}"

    report = player_games_build.render_final_audit(
        manifest,
        manifest_sha256=digest,
        snapshot_name=snapshot_name,
        restore_verified=True,
    )

    assert digest in report
    assert snapshot_name in report
    assert "Restore verified: yes" in report
    assert "Trusted publication: ending seasons 2012–2026" in report
    assert "Consumed player-log cache identities: 37" in report
    assert "Publication partitions: 15" in report
    assert "Raw archive: ending seasons 1984–2026 (93 identities)" in report
    assert "Historical quarantine: ending seasons 1984–2011" in report
    assert "Raw player rows audited: 1,082,012" in report
    assert "Source games audited: 52,529" in report
    assert "Team-game sides audited: 105,058" in report
    assert "Point candidates: 662" in report
    assert "Minute candidates: 1,211" in report
    assert "Unique affected team-game keys: 1,300" in report
    assert "Unique affected games: 1,164" in report
    assert "Cross-section overlaps: 573" in report
    assert (
        "Point causes: 627 incomplete payloads; 27 bad references; "
        "7 phantom rows; 1 isolated discrepancy"
    ) in report
    assert (
        "Minute causes: 573 point-deficit overlaps; 264 active MIN=0 sides; "
        "228 residual-six-to-eight sides; 146 other anomalies"
    ) in report
    assert "Approved point exceptions: 0" in report
    assert "Approved minute exceptions: 0" in report
    assert "Applied matchup repairs: 0" in report
    assert "Applied designation repairs: 10" in report
    assert "3e62e215555898ddade63322688760c24ba6c3a5d2c7f4b0face1169fd47dbd0" in report
    assert "ade10d5af96f47ba633b8526488eb9cb94607ce024aa0ce7e0b63ed9b05d3388" in report

    with pytest.raises(player_games_build.PlayerGamesBuildError, match="hash"):
        player_games_build.render_final_audit(
            manifest,
            manifest_sha256="0" * 64,
            snapshot_name=snapshot_name,
            restore_verified=True,
        )


@pytest.mark.parametrize(
    "snapshot_name",
    [
        "player-games.tar.zst",
        "player-games-archive-2026-000000000000",
        "other-player-games-archive-2026-{sha12}",
    ],
)
def test_render_final_audit_rejects_unbound_snapshot_names(snapshot_name):
    """Would fail if final evidence accepted a prefixless or wrong snapshot."""
    manifest = _conforming_final_manifest()
    digest = hashlib.sha256(
        player_games_build._manifest_bytes(manifest)
    ).hexdigest()
    candidate = snapshot_name.format(sha12=digest[:12])

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match="snapshot name",
    ):
        player_games_build.render_final_audit(
            manifest,
            manifest_sha256=digest,
            snapshot_name=candidate,
            restore_verified=True,
        )


def test_render_final_audit_requires_restore_verified_true():
    """Would fail if final evidence could be rendered without a verified restore."""
    manifest = _conforming_final_manifest()
    digest = hashlib.sha256(
        player_games_build._manifest_bytes(manifest)
    ).hexdigest()
    snapshot_name = f"player-games-archive-2026-{digest[:12]}"

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match="verified restore",
    ):
        player_games_build.render_final_audit(
            manifest,
            manifest_sha256=digest,
            snapshot_name=snapshot_name,
            restore_verified=False,
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda manifest: manifest["scope"].__setitem__(
            "ending_seasons", [2013] + list(range(2013, 2027))
        ),
        lambda manifest: manifest["sources"]["player_logs"].pop(),
        lambda manifest: manifest["partitions"].pop("2012"),
        lambda manifest: manifest["validation"]["exceptions"].__setitem__(
            "points", 1
        ),
        lambda manifest: manifest["validation"]["exceptions"].__setitem__(
            "minutes", 1
        ),
        lambda manifest: manifest["validation"]["matchup_repairs"].__setitem__(
            "count", 1
        ),
        lambda manifest: manifest["validation"]["designation_repairs"].__setitem__(
            "count", 9
        ),
    ],
    ids=[
        "scope",
        "source-count",
        "partition-count",
        "point-exceptions",
        "minute-exceptions",
        "matchup-repairs",
        "designation-repairs",
    ],
)
def test_render_final_audit_rejects_manifests_that_drift_from_the_approved_release(
    mutate,
):
    """Would fail if final evidence could be rendered from a manifest whose
    scope, source count, partition count, exception counts, or repair counts
    differ from the amendment's approved [2012..2026] / 37 / 15 / 0/0 / 0/10
    release."""
    manifest = _conforming_final_manifest()
    mutate(manifest)
    payload = player_games_build._manifest_bytes(manifest)
    digest = hashlib.sha256(payload).hexdigest()
    snapshot_name = f"player-games-archive-2026-{digest[:12]}"

    with pytest.raises(player_games_build.PlayerGamesBuildError):
        player_games_build.render_final_audit(
            manifest,
            manifest_sha256=digest,
            snapshot_name=snapshot_name,
            restore_verified=True,
        )


def test_audit_script_final_report_matches_render_final_audit_output(
    monkeypatch, tmp_path
):
    """Would fail if the CLI final-report path reformatted or diverged from
    the exact string `render_final_audit` returns for the same manifest hash,
    snapshot name, and restore flag."""
    from scripts import audit_player_logs

    manifest = _conforming_final_manifest(rows=406843, games=19183, players=1862)
    publication_dir = tmp_path / "parquet-large/player_games"
    publication_dir.mkdir(parents=True)
    manifest_bytes = player_games_build._manifest_bytes(manifest)
    (publication_dir / "_manifest.json").write_bytes(manifest_bytes)
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    snapshot_name = f"player-games-archive-2026-{digest[:12]}"

    monkeypatch.setattr(
        audit_player_logs.player_games_build,
        "validate_publication",
        lambda *_args: manifest,
    )

    report = tmp_path / "final.md"
    audit_player_logs.main([
        "--data-dir", str(tmp_path),
        "--output-dir", str(tmp_path / "unused-review"),
        "--final-report", str(report),
        "--manifest-sha256", digest,
        "--snapshot-name", snapshot_name,
        "--restore-verified",
    ])

    expected = player_games_build.render_final_audit(
        manifest,
        manifest_sha256=digest,
        snapshot_name=snapshot_name,
        restore_verified=True,
    )
    assert report.read_text(encoding="utf-8") == expected


def test_audit_script_normal_mode_is_offline_source_review(monkeypatch, tmp_path):
    """Would fail if the audit command imported or called a network path."""
    import requests

    def network_forbidden(*args, **kwargs):
        raise AssertionError("network access is forbidden during source review")

    monkeypatch.setattr(requests.Session, "get", network_forbidden)
    from scripts import audit_player_logs

    audit = {"fixture": True}
    calls = []
    monkeypatch.setattr(
        audit_player_logs.player_games_build,
        "audit_source",
        lambda data_dir: calls.append(("audit", data_dir)) or audit,
    )
    monkeypatch.setattr(
        audit_player_logs.player_games_build,
        "write_source_review",
        lambda output_dir, value: calls.append(("write", output_dir, value)),
    )

    audit_player_logs.main([
        "--data-dir", str(tmp_path),
        "--output-dir", str(tmp_path / "review"),
    ])

    assert calls == [
        ("audit", tmp_path),
        ("write", tmp_path / "review", audit),
    ]


def test_audit_script_preserves_symlink_identity_for_source_output(
    monkeypatch, tmp_path
):
    """Would fail if wrapper resolution bypassed output-dir lstat safety."""
    _use_tiny_scope(monkeypatch)
    _publish_scope(tmp_path)
    _publish_references(tmp_path)
    _use_empty_exception_ledgers(monkeypatch, tmp_path)
    from scripts import audit_player_logs

    target = tmp_path / "target"
    target.mkdir()
    output = tmp_path / "review-link"
    output.symlink_to(target, target_is_directory=True)

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match="not a real directory",
    ):
        audit_player_logs.main([
            "--data-dir", str(tmp_path),
            "--output-dir", str(output),
        ])

    assert list(target.iterdir()) == []


def test_audit_script_final_mode_requires_complete_verified_binding(
    monkeypatch, tmp_path
):
    """Would fail if final audit rendering accepted partial evidence."""
    from scripts import audit_player_logs

    publication_dir = tmp_path / "parquet-large/player_games"
    publication_dir.mkdir(parents=True)
    manifest = _conforming_final_manifest()
    manifest_bytes = player_games_build._manifest_bytes(manifest)
    (publication_dir / "_manifest.json").write_bytes(manifest_bytes)
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    calls = []
    monkeypatch.setattr(
        audit_player_logs.player_games_build,
        "validate_publication",
        lambda raw_root, output, references: calls.append(
            (raw_root, output, references)
        ) or manifest,
    )

    report = tmp_path / "final.md"
    audit_player_logs.main([
        "--data-dir", str(tmp_path),
        "--output-dir", str(tmp_path / "unused-review"),
        "--final-report", str(report),
        "--manifest-sha256", digest,
        "--snapshot-name", f"player-games-archive-2026-{digest[:12]}",
        "--restore-verified",
    ])

    assert calls == [(
        tmp_path / "raw/stats_nba/player_logs",
        publication_dir,
        tmp_path / "parquet",
    )]
    assert digest in report.read_text(encoding="utf-8")

    report.write_text("safe existing regular file", encoding="utf-8")
    audit_player_logs.main([
        "--data-dir", str(tmp_path),
        "--output-dir", str(tmp_path / "unused-review"),
        "--final-report", str(report),
        "--manifest-sha256", digest,
        "--snapshot-name", f"player-games-archive-2026-{digest[:12]}",
        "--restore-verified",
    ])
    assert digest in report.read_text(encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        audit_player_logs.main([
            "--data-dir", str(tmp_path),
            "--output-dir", str(tmp_path / "unused-review"),
            "--final-report", str(tmp_path / "incomplete.md"),
        ])
    assert exc_info.value.code == 2


def test_audit_script_rejects_symlink_final_report(monkeypatch, tmp_path):
    """Would fail if final-report writes followed a symlink target."""
    from scripts import audit_player_logs

    publication_dir = tmp_path / "parquet-large/player_games"
    publication_dir.mkdir(parents=True)
    manifest = _conforming_final_manifest()
    manifest_bytes = player_games_build._manifest_bytes(manifest)
    (publication_dir / "_manifest.json").write_bytes(manifest_bytes)
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    monkeypatch.setattr(
        audit_player_logs.player_games_build,
        "validate_publication",
        lambda *_args: manifest,
    )
    sentinel = tmp_path / "sentinel.md"
    sentinel.write_text("do not overwrite", encoding="utf-8")
    report = tmp_path / "final-link.md"
    report.symlink_to(sentinel)

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match="final report.*regular file",
    ):
        audit_player_logs.main([
            "--data-dir", str(tmp_path),
            "--output-dir", str(tmp_path / "unused-review"),
            "--final-report", str(report),
            "--manifest-sha256", digest,
            "--snapshot-name", f"player-games-archive-2026-{digest[:12]}",
            "--restore-verified",
        ])

    assert sentinel.read_text(encoding="utf-8") == "do not overwrite"


def test_audit_script_parser_rejects_incomplete_final_mode_in_subprocess(
    tmp_path,
):
    """Would fail if CLI parsing treated partial final evidence as review mode."""
    script = Path(__file__).parents[1] / "scripts/audit_player_logs.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--data-dir", str(tmp_path),
            "--output-dir", str(tmp_path / "review"),
            "--final-report", str(tmp_path / "final.md"),
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "must be supplied together" in completed.stderr
