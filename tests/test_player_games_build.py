import json
import hashlib
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
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
