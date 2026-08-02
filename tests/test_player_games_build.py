import json
import hashlib
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pandas as pd
import pytest

from nba_data import cache, player_games_build, player_logs


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
        monkeypatch.setattr(module, "SEASONS", (1984, 1985))
        monkeypatch.setattr(module, "PLAY_IN_SEASONS", ())


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

    table = player_games_build._assemble_seasons(tmp_path)

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


def test_stage_partitions_writes_exact_zstd_deterministic_season_trees(
    monkeypatch, tmp_path,
):
    """Would fail if staging inferred schema or emitted unstable Parquet."""
    _use_tiny_scope(monkeypatch)
    _publish_scope(tmp_path)
    validate_inventory = player_logs.validate_cache_inventory
    validated = []

    def record_validation(data_dir):
        validated.append(data_dir)
        return validate_inventory(data_dir)

    monkeypatch.setattr(player_logs, "validate_cache_inventory", record_validation)
    stages = [tmp_path / "stage-a", tmp_path / "stage-b"]
    for stage in stages:
        stage.mkdir()

    for stage in stages:
        player_games_build.stage_partitions(tmp_path, stage)

    expected_files = {
        "season=1984/player_games.parquet",
        "season=1985/player_games.parquet",
    }
    assert {
        path.relative_to(stages[0]).as_posix()
        for path in stages[0].rglob("*")
        if path.is_file()
    } == expected_files
    assert validated == [tmp_path, tmp_path]
    for season in (1984, 1985):
        paths = [stage / f"season={season}/player_games.parquet" for stage in stages]
        tables = [pq.read_table(path) for path in paths]
        assert all(table.schema == player_games_build.PLAYER_GAMES_ARROW_SCHEMA for table in tables)
        assert all(
            pq.ParquetFile(path).metadata.row_group(0).column(column).compression
            == "ZSTD"
            for path in paths
            for column in range(len(player_logs.PLAYER_GAME_COLUMNS))
        )
        assert hashlib.sha256(paths[0].read_bytes()).hexdigest() == (
            hashlib.sha256(paths[1].read_bytes()).hexdigest()
        )


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


@pytest.mark.parametrize("unsafe_kind", ["file", "symlink-season"])
def test_stage_partitions_rejects_every_preexisting_stage_entry(
    monkeypatch, tmp_path, unsafe_kind,
):
    """Would fail if staging merged with an untrusted prior tree."""
    stage = tmp_path / "stage"
    stage.mkdir()
    if unsafe_kind == "file":
        (stage / "stray.txt").write_text("unexpected")
    else:
        target = tmp_path / "outside"
        target.mkdir()
        (stage / "season=1984").symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(
        player_games_build.player_logs,
        "validate_cache_inventory",
        lambda *args: pytest.fail("inventory inspected before rejecting stage"),
    )

    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match="stage directory must be empty",
    ):
        player_games_build.stage_partitions(tmp_path, stage)


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


def _validated_coverage():
    aggregates = player_games_build.aggregate_team_games(_player_game_rows())
    return player_games_build.cross_validate_coverage(
        aggregates, *_coverage_references()
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
            aggregates, *_coverage_references()
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
            aggregates, *_coverage_references()
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
        player_games_build.aggregate_team_games(rows), *_coverage_references()
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
        player_games_build.aggregate_team_games(rows), *_coverage_references()
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
    ]
    assert len(audit["player_games"]) == 8
    assert len(audit["team_aggregates"]) == 8
    assert len(audit["validated_team_games"]) == 8
    assert len(audit["discrepancies"]["minutes"]) == 8
    assert audit["discrepancies"]["points"] == {}
    assert audit["approved_exceptions"] == _ledger()
