"""Offline assembly and staging for normalized player-game logs."""

from __future__ import annotations

import json
from pathlib import Path
import stat

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from nba_data import cache, player_logs


SEASONS = player_logs.SEASONS
PLAY_IN_SEASONS = player_logs.PLAY_IN_SEASONS
EXCEPTIONS_PATH = (
    Path(__file__).resolve().parent
    / "reference/player_game_validation_exceptions.json"
)


PLAYER_GAMES_ARROW_SCHEMA = pa.schema(
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


class PlayerGamesBuildError(ValueError):
    """Raised when player-game assembly or staging violates its contract."""


_EXCEPTION_FIELDS = {
    "points": {
        "game_id", "franchise_id", "player_total", "published_total",
        "season", "note",
    },
    "minutes": {
        "game_id", "franchise_id", "player_total", "opponent_total",
        "inferred_overtime_periods", "published_overtime_periods",
        "reason", "season", "note",
    },
}
_NULLABLE_INTEGER_FIELDS = {
    "player_total", "opponent_total", "inferred_overtime_periods",
    "published_overtime_periods",
}
_REQUIRED_INTEGER_FIELDS = {"published_total", "season"}
_STRING_FIELDS = {"game_id", "franchise_id", "reason", "note"}


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise PlayerGamesBuildError(
                f"duplicate semantic identity in exception ledger: {key!r}"
            )
        result[key] = value
    return result


def _validate_exception_entry(section: str, key: str, entry: object) -> None:
    if not isinstance(key, str):
        raise PlayerGamesBuildError(
            f"{section} exception has malformed composite key: {key!r}"
        )
    parts = key.split("|")
    if (
        len(parts) != 2
        or len(parts[0]) != 10
        or not parts[0].isdigit()
        or not parts[1]
        or parts[1].strip() != parts[1]
    ):
        raise PlayerGamesBuildError(
            f"{section} exception has malformed composite key: {key!r}"
        )
    if not isinstance(entry, dict):
        raise PlayerGamesBuildError(
            f"{section} exception {key!r} must be an object"
        )
    if set(entry) != _EXCEPTION_FIELDS[section]:
        raise PlayerGamesBuildError(
            f"{section} exception {key!r} has invalid fields: "
            f"{sorted(entry)}"
        )

    for field in _STRING_FIELDS & _EXCEPTION_FIELDS[section]:
        if type(entry[field]) is not str:
            raise PlayerGamesBuildError(
                f"{section} exception {key!r} field {field!r} has wrong type"
            )
    for field in _REQUIRED_INTEGER_FIELDS & _EXCEPTION_FIELDS[section]:
        if type(entry[field]) is not int:
            raise PlayerGamesBuildError(
                f"{section} exception {key!r} field {field!r} has wrong type"
            )
    for field in _NULLABLE_INTEGER_FIELDS & _EXCEPTION_FIELDS[section]:
        if entry[field] is not None and type(entry[field]) is not int:
            raise PlayerGamesBuildError(
                f"{section} exception {key!r} field {field!r} has wrong type"
            )

    if not entry["note"].strip():
        raise PlayerGamesBuildError(
            f"{section} exception {key!r} requires a non-empty note"
        )
    identity = f"{entry['game_id']}|{entry['franchise_id']}"
    if identity != key:
        raise PlayerGamesBuildError(
            f"{section} exception key/body identity mismatch: "
            f"{key!r} != {identity!r}"
        )


def load_exceptions(path: Path = EXCEPTIONS_PATH) -> dict:
    """Load the player-game validation exception ledger."""
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON numeric constant: {value}")

    try:
        document = json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=reject_constant,
        )
    except PlayerGamesBuildError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as exc:
        raise PlayerGamesBuildError(
            f"invalid JSON in exception ledger {path}: {exc}"
        ) from exc

    if not isinstance(document, dict):
        raise PlayerGamesBuildError("exception ledger root must be an object")
    if set(document) != {"minutes", "points"}:
        raise PlayerGamesBuildError(
            "exception ledger top-level keys must be exactly "
            "['minutes', 'points']"
        )
    for section in ("minutes", "points"):
        entries = document[section]
        if not isinstance(entries, dict):
            raise PlayerGamesBuildError(
                f"exception ledger {section} must be an object"
            )
        for key, entry in entries.items():
            _validate_exception_entry(section, key, entry)
    return document


def _applicable_game_types(season: int) -> tuple[str, ...]:
    if season in PLAY_IN_SEASONS:
        return player_logs.GAME_TYPES
    return player_logs.GAME_TYPES[:2]


def _table_from_frame(frame: pd.DataFrame) -> pa.Table:
    required = [field.name for field in PLAYER_GAMES_ARROW_SCHEMA if not field.nullable]
    null_columns = [name for name in required if frame[name].isna().any()]
    if null_columns:
        raise PlayerGamesBuildError(
            f"required columns contain nulls: {null_columns}"
        )
    try:
        return pa.Table.from_pandas(
            frame,
            schema=PLAYER_GAMES_ARROW_SCHEMA,
            preserve_index=False,
            safe=True,
        )
    except (pa.ArrowException, TypeError, ValueError) as exc:
        raise PlayerGamesBuildError(f"Arrow conversion failed: {exc}") from exc


def _assemble_season(data_dir: Path, season: int) -> pa.Table:
    frames = []
    for game_type in _applicable_game_types(season):
        cached = player_logs.read_cached(Path(data_dir), season, game_type)
        if cached is None:
            raise cache.CacheIntegrityError(
                f"missing player-log cache entry for season {season} "
                f"game type {game_type!r}"
            )
        payload, _metadata = cached
        frames.append(
            player_logs.normalize_payload(
                payload, season=season, game_type=game_type
            )
        )

    frame = pd.concat(frames, ignore_index=True)
    if frame.duplicated(["game_id", "person_id"]).any():
        raise PlayerGamesBuildError(
            f"season {season} has duplicate (game_id, person_id)"
        )
    frame = frame.sort_values(
        ["season", "game_date", "game_id", "franchise_id", "person_id"],
        kind="stable",
    ).reset_index(drop=True)
    return _table_from_frame(frame)


def _assemble_seasons(data_dir: Path) -> pa.Table:
    tables = [_assemble_season(data_dir, season) for season in SEASONS]
    return pa.concat_tables(tables)


def _require_empty_stage(stage_dir: Path) -> None:
    try:
        mode = stage_dir.lstat().st_mode
    except FileNotFoundError as exc:
        raise PlayerGamesBuildError(
            f"stage directory does not exist: {stage_dir}"
        ) from exc
    if not stat.S_ISDIR(mode):
        raise PlayerGamesBuildError(
            f"stage path is not a real directory: {stage_dir}"
        )
    if any(stage_dir.iterdir()):
        raise PlayerGamesBuildError(
            f"stage directory must be empty: {stage_dir}"
        )


def _require_zstd_compression(path: Path) -> None:
    metadata = pq.ParquetFile(path).metadata
    for row_group_index in range(metadata.num_row_groups):
        row_group = metadata.row_group(row_group_index)
        for column_index in range(row_group.num_columns):
            compression = row_group.column(column_index).compression
            if compression != "ZSTD":
                raise PlayerGamesBuildError(
                    f"partition {path} row group {row_group_index} column "
                    f"{column_index} is not ZSTD-compressed: {compression}"
                )


def _validate_partition(
    expected: pa.Table, partition_path: Path, *, season: int
) -> pa.Table:
    reread = pq.read_table(partition_path)
    if reread.schema != PLAYER_GAMES_ARROW_SCHEMA:
        raise PlayerGamesBuildError(
            f"season {season} partition schema does not match "
            "PLAYER_GAMES_ARROW_SCHEMA"
        )
    if not reread.equals(expected):
        raise PlayerGamesBuildError(
            f"season {season} partition rows do not match assembled rows"
        )
    keys = reread.select(["game_id", "person_id"]).to_pandas()
    if keys.duplicated(["game_id", "person_id"]).any():
        raise PlayerGamesBuildError(
            f"season {season} has duplicate (game_id, person_id)"
        )
    _require_zstd_compression(partition_path)
    return reread


def _write_partition(table: pa.Table, stage_dir: Path, season: int) -> Path:
    partition_dir = stage_dir / f"season={season}"
    try:
        partition_dir.lstat()
    except FileNotFoundError:
        pass
    else:
        raise PlayerGamesBuildError(
            f"season partition path already exists: {partition_dir}"
        )
    partition_dir.mkdir()
    partition_path = partition_dir / "player_games.parquet"
    pq.write_table(
        table,
        partition_path,
        compression="zstd",
        use_dictionary=False,
        write_statistics=True,
    )
    _validate_partition(table, partition_path, season=season)
    return partition_path


def stage_partitions(data_dir: Path, stage_dir: Path) -> tuple[Path, ...]:
    """Validate raw inventory and stage one exact Parquet file per season."""
    data_dir = Path(data_dir)
    stage_dir = Path(stage_dir)
    _require_empty_stage(stage_dir)
    player_logs.validate_cache_inventory(data_dir)
    return tuple(
        _write_partition(_assemble_season(data_dir, season), stage_dir, season)
        for season in SEASONS
    )
