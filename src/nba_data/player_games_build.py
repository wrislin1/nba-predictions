"""Offline assembly and staging for normalized player-game logs."""

from __future__ import annotations

import json
import hashlib
import importlib.metadata
from pathlib import Path
import shutil
import stat
import sys
import tempfile

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from nba_data import cache, player_logs, publication


SEASONS = player_logs.SEASONS
PLAY_IN_SEASONS = player_logs.PLAY_IN_SEASONS
MINUTE_TOLERANCE = 5
MAX_OVERTIME_PERIODS = 6
EXCEPTIONS_PATH = (
    Path(__file__).resolve().parent
    / "reference/player_game_validation_exceptions.json"
)
FRANCHISE_REFERENCE_PATH = (
    Path(__file__).resolve().parent / "reference/franchises.csv"
)
REFERENCE_FILENAMES = (
    "build_manifest.json",
    "games.parquet",
    "playoff_games.parquet",
    "playoff_games_manifest.json",
    "team_games.parquet",
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


_TEAM_GAME_IDENTITY = [
    "game_id",
    "season",
    "game_type",
    "game_date",
    "franchise_id",
    "opponent_franchise_id",
    "is_home",
]
_POSTSEASON_GAME_IDENTITY = [
    "season",
    "game_date",
    "game_type",
    "home_franchise_id",
    "away_franchise_id",
]


def aggregate_team_games(player_games: pd.DataFrame) -> pd.DataFrame:
    """Aggregate normalized player rows without converting incomplete sums to zero."""
    required = set(_TEAM_GAME_IDENTITY) | {"pts", "min"}
    missing = sorted(required - set(player_games.columns))
    if missing:
        raise PlayerGamesBuildError(
            f"player games missing aggregation columns: {missing}"
        )

    rows = []
    for identity, group in player_games.groupby(
        _TEAM_GAME_IDENTITY, sort=False, dropna=False
    ):
        points_complete = group["pts"].notna().all()
        minutes_complete = group["min"].notna().all()
        row = dict(zip(_TEAM_GAME_IDENTITY, identity, strict=True))
        row.update(
            player_count=int(len(group)),
            points_complete=bool(points_complete),
            player_total=(int(group["pts"].sum()) if points_complete else None),
            minutes_complete=bool(minutes_complete),
            minute_total=(int(group["min"].sum()) if minutes_complete else None),
        )
        rows.append(row)
    return pd.DataFrame(rows).sort_values(
        ["game_id", "franchise_id"], kind="stable"
    ).reset_index(drop=True)


def _team_keys(frame: pd.DataFrame) -> list[str]:
    if frame.empty:
        return []
    return sorted(
        f"{row.game_id}|{row.franchise_id}"
        for row in frame[["game_id", "franchise_id"]].itertuples(index=False)
    )


def _postseason_keys(frame: pd.DataFrame) -> list[str]:
    if frame.empty:
        return []
    return sorted(
        "|".join(str(value) for value in row)
        for row in frame[_POSTSEASON_GAME_IDENTITY].itertuples(
            index=False, name=None
        )
    )


def _require_unique(
    frame: pd.DataFrame, key: list[str], label: str, *, team_key: bool = False
) -> None:
    duplicates = frame.loc[frame.duplicated(key, keep=False)]
    if duplicates.empty:
        return
    if team_key:
        identities = _team_keys(duplicates)
    else:
        identities = sorted(
            "|".join(str(value) for value in row)
            for row in duplicates[key].itertuples(index=False, name=None)
        )
    raise PlayerGamesBuildError(
        f"duplicate {label} identity: {identities}"
    )


def _regular_game_sides(games: pd.DataFrame) -> pd.DataFrame:
    _require_unique(games, ["game_id"], "regular game")
    common = games[["game_id", "season", "game_date"]]
    home = common.assign(
        game_type="regular",
        franchise_id=games["home_franchise_id"],
        opponent_franchise_id=games["away_franchise_id"],
        is_home=True,
        published_total=games["home_pts"],
    )
    away = common.assign(
        game_type="regular",
        franchise_id=games["away_franchise_id"],
        opponent_franchise_id=games["home_franchise_id"],
        is_home=False,
        published_total=games["away_pts"],
    )
    return pd.concat([home, away], ignore_index=True)


def _compare_regular_references(
    game_sides: pd.DataFrame, team_games: pd.DataFrame
) -> pd.DataFrame:
    team_reference = team_games.assign(game_type="regular").rename(
        columns={"pts": "published_total"}
    )[
        _TEAM_GAME_IDENTITY + ["published_total"]
    ].copy()
    _require_unique(
        game_sides, ["game_id", "franchise_id"], "games reference", team_key=True
    )
    _require_unique(
        team_reference,
        ["game_id", "franchise_id"],
        "team-games reference",
        team_key=True,
    )
    comparison = game_sides.merge(
        team_reference,
        on=_TEAM_GAME_IDENTITY + ["published_total"],
        how="outer",
        indicator=True,
        validate="one_to_one",
    )
    if not comparison["_merge"].eq("both").all():
        raise PlayerGamesBuildError(
            "regular games/team_games identity mismatch: "
            f"games={_team_keys(comparison.loc[comparison['_merge'].eq('left_only')])}, "
            f"team_games={_team_keys(comparison.loc[comparison['_merge'].eq('right_only')])}"
        )
    return game_sides


def _validate_regular_coverage(
    aggregates: pd.DataFrame, expected: pd.DataFrame
) -> pd.DataFrame:
    key = ["game_id", "franchise_id"]
    _require_unique(aggregates, key, "regular player aggregate", team_key=True)
    comparison = aggregates.merge(
        expected,
        on=key,
        how="outer",
        suffixes=("", "_published"),
        indicator=True,
        validate="one_to_one",
    )
    unmatched = comparison.loc[comparison["_merge"].ne("both")]
    if not unmatched.empty:
        raise PlayerGamesBuildError(
            "regular player-game coverage mismatch: "
            f"identities={_team_keys(unmatched)}"
        )
    drift = pd.Series(False, index=comparison.index)
    for column in (
        "season", "game_type", "game_date", "opponent_franchise_id", "is_home"
    ):
        drift |= comparison[column].ne(comparison[f"{column}_published"])
    if drift.any():
        raise PlayerGamesBuildError(
            "regular team-game identity drift: "
            f"identities={_team_keys(comparison.loc[drift])}"
        )
    return comparison[
        list(aggregates.columns) + ["published_total"]
    ].copy()


def _pivot_postseason_aggregates(aggregates: pd.DataFrame) -> pd.DataFrame:
    home = aggregates.loc[aggregates["is_home"]].copy()
    away = aggregates.loc[~aggregates["is_home"]].copy()
    _require_unique(home, ["game_id"], "postseason home aggregate", team_key=True)
    _require_unique(away, ["game_id"], "postseason away aggregate", team_key=True)
    paired = home.merge(
        away,
        on="game_id",
        how="outer",
        suffixes=("_home", "_away"),
        indicator=True,
        validate="one_to_one",
    )
    if not paired["_merge"].eq("both").all():
        raise PlayerGamesBuildError(
            "postseason player-game side mismatch: "
            f"identities={sorted(paired['game_id'].astype(str).tolist())}"
        )
    consistency = (
        paired["season_home"].eq(paired["season_away"])
        & paired["game_type_home"].eq(paired["game_type_away"])
        & paired["game_date_home"].eq(paired["game_date_away"])
        & paired["franchise_id_home"].eq(paired["opponent_franchise_id_away"])
        & paired["franchise_id_away"].eq(paired["opponent_franchise_id_home"])
    )
    if not consistency.all():
        raise PlayerGamesBuildError(
            "postseason player-game identity drift: "
            f"game_ids={sorted(paired.loc[~consistency, 'game_id'].astype(str))}"
        )
    return pd.DataFrame(
        {
            "player_game_id": paired["game_id"],
            "season": paired["season_home"],
            "game_date": paired["game_date_home"],
            "game_type": paired["game_type_home"],
            "home_franchise_id": paired["franchise_id_home"],
            "away_franchise_id": paired["franchise_id_away"],
        }
    )


def _validate_postseason_coverage(
    aggregates: pd.DataFrame, playoff_games: pd.DataFrame
) -> pd.DataFrame:
    pivoted = _pivot_postseason_aggregates(aggregates)
    reference_columns = _POSTSEASON_GAME_IDENTITY + [
        "game_id", "home_pts", "away_pts", "overtime_periods"
    ]
    reference = playoff_games[reference_columns].rename(
        columns={"game_id": "reference_game_id"}
    )
    _require_unique(
        pivoted, _POSTSEASON_GAME_IDENTITY, "postseason player composite"
    )
    _require_unique(
        reference, _POSTSEASON_GAME_IDENTITY, "postseason publication composite"
    )
    comparison = pivoted.merge(
        reference,
        on=_POSTSEASON_GAME_IDENTITY,
        how="outer",
        indicator=True,
        validate="one_to_one",
    )
    unmatched = comparison.loc[comparison["_merge"].ne("both")]
    if not unmatched.empty:
        player_only = comparison.loc[comparison["_merge"].eq("left_only")]
        publication_only = comparison.loc[comparison["_merge"].eq("right_only")]
        raise PlayerGamesBuildError(
            "postseason player-game coverage mismatch: "
            f"player_game_ids={sorted(player_only['player_game_id'].dropna().astype(str))}, "
            f"publication={_postseason_keys(publication_only)}, "
            f"player_composites={_postseason_keys(player_only)}"
        )

    enrichment = comparison[
        ["player_game_id", "reference_game_id", "home_pts", "away_pts", "overtime_periods"]
    ]
    result = aggregates.merge(
        enrichment,
        left_on="game_id",
        right_on="player_game_id",
        how="left",
        validate="many_to_one",
    )
    if result["reference_game_id"].isna().any():
        raise PlayerGamesBuildError("postseason enrichment discarded coverage evidence")
    result["published_total"] = result["home_pts"].where(
        result["is_home"], result["away_pts"]
    )
    result["published_overtime_periods"] = result["overtime_periods"]
    return result[
        list(aggregates.columns)
        + ["published_total", "published_overtime_periods", "reference_game_id"]
    ].copy()


def cross_validate_coverage(
    team_aggregates: pd.DataFrame,
    games: pd.DataFrame,
    team_games: pd.DataFrame,
    playoff_games: pd.DataFrame,
) -> pd.DataFrame:
    """Require exact regular and source-independent postseason coverage."""
    scoped = set(range(1984, 2027))
    regular_aggregates = team_aggregates.loc[
        team_aggregates["season"].isin(scoped)
        & team_aggregates["game_type"].eq("regular")
    ].copy()
    regular_games = games.loc[games["season"].isin(scoped)].copy()
    regular_team_games = team_games.loc[
        team_games["season"].isin(scoped)
    ].copy()
    regular_expected = _compare_regular_references(
        _regular_game_sides(regular_games), regular_team_games
    )
    regular = _validate_regular_coverage(regular_aggregates, regular_expected)
    regular["published_overtime_periods"] = pd.NA
    regular["reference_game_id"] = regular["game_id"]

    postseason_aggregates = team_aggregates.loc[
        team_aggregates["season"].isin(scoped)
        & team_aggregates["game_type"].isin(["playoff", "play_in"])
    ].copy()
    postseason_reference = playoff_games.loc[
        playoff_games["season"].isin(scoped)
        & playoff_games["game_type"].isin(["playoff", "play_in"])
    ].copy()
    postseason = _validate_postseason_coverage(
        postseason_aggregates, postseason_reference
    )
    columns = list(team_aggregates.columns) + [
        "published_total", "published_overtime_periods", "reference_game_id"
    ]
    return pd.concat(
        [regular[columns], postseason[columns]], ignore_index=True
    ).sort_values(["game_id", "franchise_id"], kind="stable").reset_index(drop=True)


def _optional_int(value: object) -> int | None:
    return None if pd.isna(value) else int(value)


def _infer_overtime(total: int) -> tuple[int, int]:
    candidates = {
        overtime: abs(total - (240 + 25 * overtime))
        for overtime in range(MAX_OVERTIME_PERIODS + 1)
    }
    minimum = min(candidates.values())
    inferred_overtime = next(
        overtime
        for overtime, residual in candidates.items()
        if residual == minimum
    )
    return inferred_overtime, minimum


def _minutes_reason(
    *,
    total: int | None,
    opponent_total: int | None,
    inferred: int | None,
    opponent_inferred: int | None,
    residual: int | None,
    game_type: str,
    published_overtime: int | None,
) -> str | None:
    if total is None:
        return "player minute total is incomplete"
    if opponent_total is None:
        return "opponent minute total is incomplete"
    if total > 240 + 25 * MAX_OVERTIME_PERIODS + MINUTE_TOLERANCE:
        return "minute total exceeds six-overtime ceiling"
    if residual is not None and residual > MINUTE_TOLERANCE:
        return (
            f"minute total residual {residual} exceeds tolerance "
            f"{MINUTE_TOLERANCE}"
        )
    if inferred != opponent_inferred:
        return "opposing teams infer different overtime periods"
    if game_type in {"playoff", "play_in"} and inferred != published_overtime:
        return "inferred/published overtime periods disagree"
    return None


def collect_discrepancies(validated_team_games: pd.DataFrame) -> dict:
    """Collect deterministic point and minute candidates for human review."""
    required = {
        "game_id", "season", "game_type", "franchise_id",
        "opponent_franchise_id", "player_total", "minute_total",
        "published_total", "published_overtime_periods",
    }
    missing = sorted(required - set(validated_team_games.columns))
    if missing:
        raise PlayerGamesBuildError(
            f"validated team games missing discrepancy columns: {missing}"
        )
    _require_unique(
        validated_team_games,
        ["game_id", "franchise_id"],
        "validated team game",
        team_key=True,
    )

    points = {}
    minutes = {}
    for game_id, game in validated_team_games.groupby("game_id", sort=False):
        if len(game) != 2:
            raise PlayerGamesBuildError(
                f"validated game {game_id} does not have exactly two team rows"
            )
        by_team = {row.franchise_id: row for row in game.itertuples(index=False)}
        for row in game.itertuples(index=False):
            key = f"{row.game_id}|{row.franchise_id}"
            player_total = _optional_int(row.player_total)
            published_total = _optional_int(row.published_total)
            if player_total is None or player_total != published_total:
                points[key] = {
                    "game_id": str(row.game_id),
                    "franchise_id": str(row.franchise_id),
                    "player_total": player_total,
                    "published_total": published_total,
                    "season": int(row.season),
                }

            opponent = by_team.get(row.opponent_franchise_id)
            if opponent is None or opponent.opponent_franchise_id != row.franchise_id:
                raise PlayerGamesBuildError(
                    f"validated game {game_id} has inconsistent opponent identity"
                )
            minute_total = _optional_int(row.minute_total)
            opponent_total = _optional_int(opponent.minute_total)
            if minute_total is None:
                inferred, residual = None, None
            else:
                inferred, residual = _infer_overtime(minute_total)
            if opponent_total is None:
                opponent_inferred = None
            else:
                opponent_inferred, _ = _infer_overtime(opponent_total)
            published_overtime = _optional_int(row.published_overtime_periods)
            reason = _minutes_reason(
                total=minute_total,
                opponent_total=opponent_total,
                inferred=inferred,
                opponent_inferred=opponent_inferred,
                residual=residual,
                game_type=str(row.game_type),
                published_overtime=published_overtime,
            )
            if reason is not None:
                minutes[key] = {
                    "game_id": str(row.game_id),
                    "franchise_id": str(row.franchise_id),
                    "player_total": minute_total,
                    "opponent_total": opponent_total,
                    "inferred_overtime_periods": inferred,
                    "published_overtime_periods": published_overtime,
                    "reason": reason,
                    "season": int(row.season),
                }
    return {
        "minutes": {key: minutes[key] for key in sorted(minutes)},
        "points": {key: points[key] for key in sorted(points)},
    }


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


def _validate_observed_entry(section: str, key: str, entry: object) -> None:
    if not isinstance(entry, dict):
        raise PlayerGamesBuildError(
            f"observed {section} discrepancy {key!r} must be an object"
        )
    expected = _EXCEPTION_FIELDS[section] - {"note"}
    if set(entry) != expected:
        raise PlayerGamesBuildError(
            f"observed {section} discrepancy {key!r} has invalid fields: "
            f"{sorted(entry)}"
        )
    _validate_exception_entry(section, key, {**entry, "note": "observed"})


def _without_note(entry: dict) -> dict:
    return {key: value for key, value in entry.items() if key != "note"}


def validate_exceptions(observed: dict, approved: dict) -> None:
    """Require exact bidirectional equality with the reviewed exception ledger."""
    for label, document in (("approved", approved), ("observed", observed)):
        if not isinstance(document, dict) or set(document) != {"minutes", "points"}:
            raise PlayerGamesBuildError(
                f"{label} exceptions top-level keys must be exactly "
                "['minutes', 'points']"
            )
        for section in ("minutes", "points"):
            if not isinstance(document[section], dict):
                raise PlayerGamesBuildError(
                    f"{label} exceptions {section} must be an object"
                )
            for key, entry in document[section].items():
                if label == "observed":
                    _validate_observed_entry(section, key, entry)
                else:
                    _validate_exception_entry(section, key, entry)

    errors = []
    for section in ("minutes", "points"):
        observed_entries = observed[section]
        approved_entries = approved[section]
        observed_keys = set(observed_entries)
        approved_keys = set(approved_entries)
        missing = sorted(observed_keys - approved_keys)
        stale = sorted(approved_keys - observed_keys)
        drift = sorted(
            key
            for key in observed_keys & approved_keys
            if observed_entries[key] != _without_note(approved_entries[key])
        )
        if missing:
            errors.append(
                f"{section} missing approvals="
                + repr({key: observed_entries[key] for key in missing})
            )
        if stale:
            errors.append(
                f"{section} stale approvals="
                + repr({key: approved_entries[key] for key in stale})
            )
        if drift:
            errors.append(
                f"{section} drift="
                + repr(
                    {
                        key: {
                            "observed": observed_entries[key],
                            "approved": _without_note(approved_entries[key]),
                        }
                        for key in drift
                    }
                )
            )
    if errors:
        raise PlayerGamesBuildError("exception ledger mismatch: " + "; ".join(errors))


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


def audit_source(data_dir: Path) -> dict:
    """Assemble deterministic offline evidence for review and publication gates."""
    data_dir = Path(data_dir)
    player_logs.validate_cache_inventory(data_dir)
    player_games = _assemble_seasons(data_dir).to_pandas()
    team_aggregates = aggregate_team_games(player_games)
    parquet_dir = data_dir / "parquet"
    validated_team_games = cross_validate_coverage(
        team_aggregates,
        pd.read_parquet(parquet_dir / "games.parquet"),
        pd.read_parquet(parquet_dir / "team_games.parquet"),
        pd.read_parquet(parquet_dir / "playoff_games.parquet"),
    )
    discrepancies = collect_discrepancies(validated_team_games)
    return {
        "player_games": player_games,
        "team_aggregates": team_aggregates,
        "validated_team_games": validated_team_games,
        "discrepancies": discrepancies,
        "approved_exceptions": load_exceptions(),
    }


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


def _assemble_season_from_root(raw_root: Path, season: int) -> pa.Table:
    frames = []
    for game_type in _applicable_game_types(season):
        destination = (
            raw_root / f"season={season}" / f"{game_type}.json"
        )
        cached = player_logs._read_cached_entry(
            destination, season, game_type
        )
        if cached is None:
            raise cache.CacheIntegrityError(
                f"missing player-log cache entry for {destination}"
            )
        payload, _metadata = cached
        frames.append(
            player_logs.normalize_payload(
                payload, season=season, game_type=game_type
            )
        )
    frame = pd.concat(frames, ignore_index=True).sort_values(
        ["season", "game_date", "game_id", "franchise_id", "person_id"],
        kind="stable",
    ).reset_index(drop=True)
    if frame.duplicated(["game_id", "person_id"]).any():
        raise PlayerGamesBuildError(
            f"season {season} has duplicate (game_id, person_id)"
        )
    return _table_from_frame(frame)


def _require_regular_file(path: Path, label: str) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise PlayerGamesBuildError(f"missing {label}: {path}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise PlayerGamesBuildError(
            f"{label} must be a regular file: {path}"
        )


def _audit_from_roots(
    raw_root: Path, reference_dir: Path
) -> tuple[dict, tuple[Path, ...]]:
    try:
        bodies = player_logs.validate_cache_inventory_root(raw_root)
    except cache.CacheIntegrityError as exc:
        raise PlayerGamesBuildError(
            f"source cache validation failed: {exc}"
        ) from exc
    for name in REFERENCE_FILENAMES:
        _require_regular_file(reference_dir / name, f"reference {name}")
    _require_regular_file(EXCEPTIONS_PATH, "exception ledger")
    _require_regular_file(FRANCHISE_REFERENCE_PATH, "franchise reference")

    tables = [_assemble_season_from_root(raw_root, season) for season in SEASONS]
    player_games = pa.concat_tables(tables).to_pandas()
    team_aggregates = aggregate_team_games(player_games)
    try:
        validated_team_games = cross_validate_coverage(
            team_aggregates,
            pd.read_parquet(reference_dir / "games.parquet"),
            pd.read_parquet(reference_dir / "team_games.parquet"),
            pd.read_parquet(reference_dir / "playoff_games.parquet"),
        )
    except (OSError, pa.ArrowException) as exc:
        raise PlayerGamesBuildError(
            f"committed reference publication could not be read: {exc}"
        ) from exc
    discrepancies = collect_discrepancies(validated_team_games)
    approved = load_exceptions(EXCEPTIONS_PATH)
    validate_exceptions(discrepancies, approved)
    return ({
        "player_games": player_games,
        "team_aggregates": team_aggregates,
        "validated_team_games": validated_team_games,
        "discrepancies": discrepancies,
        "approved_exceptions": approved,
    }, bodies)


def _versions() -> dict:
    return {
        "nba_api": importlib.metadata.version("nba_api"),
        "pandas": pd.__version__,
        "pyarrow": pa.__version__,
        "python": (
            f"{sys.version_info.major}.{sys.version_info.minor}."
            f"{sys.version_info.micro}"
        ),
    }


def _file_binding(path: Path, logical_path: str) -> dict:
    _require_regular_file(path, logical_path)
    payload = path.read_bytes()
    return {
        "path": logical_path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }


def _source_entry(body_path: Path, raw_root: Path) -> dict:
    sidecar_path = body_path.with_name(body_path.name + ".meta.json")
    _require_regular_file(body_path, "source body")
    _require_regular_file(sidecar_path, "source sidecar")
    body = body_path.read_bytes()
    sidecar = sidecar_path.read_bytes()
    try:
        metadata = json.loads(sidecar.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlayerGamesBuildError(
            f"source sidecar is invalid JSON: {sidecar_path}"
        ) from exc
    if not isinstance(metadata, dict):
        raise PlayerGamesBuildError(
            f"source sidecar must contain an object: {sidecar_path}"
        )
    relative = body_path.relative_to(raw_root).as_posix()
    identity = {
        key: metadata[key]
        for key in sorted(metadata)
        if key not in {"content_length", "fetched_at", "sha256"}
    }
    return {
        "body_path": relative,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "body_content_length": len(body),
        "sidecar_path": sidecar_path.relative_to(raw_root).as_posix(),
        "sidecar_sha256": hashlib.sha256(sidecar).hexdigest(),
        "sidecar_content_length": len(sidecar),
        "identity": identity,
    }


def _logical_row_sha256(table: pa.Table) -> str:
    """Hash typed logical rows independently of their Parquet encoding."""
    table = table.combine_chunks()
    if table.schema.metadata:
        table = table.replace_schema_metadata(None)
    digest = hashlib.sha256()
    digest.update(PLAYER_GAMES_ARROW_SCHEMA.serialize().to_pybytes())
    for batch in table.to_batches(max_chunksize=65_536):
        if batch.schema.metadata:
            batch = batch.replace_schema_metadata(None)
        payload = batch.serialize().to_pybytes()
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _partition_entry(table: pa.Table, path: Path, season: int) -> dict:
    frame = table.select(
        ["game_id", "person_id", "game_type"]
    ).to_pandas()
    game_counts = (
        frame[["game_id", "game_type"]]
        .drop_duplicates()["game_type"]
        .value_counts()
    )
    return {
        "path": f"season={season}/player_games.parquet",
        "rows": int(table.num_rows),
        "players": int(frame["person_id"].nunique()),
        "game_counts": {
            game_type: int(game_counts.get(game_type, 0))
            for game_type in sorted(_applicable_game_types(season))
        },
        "bytes": int(path.stat().st_size),
        "sha256": cache.sha256_file(path),
        "row_sha256": _logical_row_sha256(table),
    }


def _schema_manifest() -> list[dict]:
    return [
        {
            "name": field.name,
            "arrow_type": str(field.type),
            "nullable": bool(field.nullable),
        }
        for field in PLAYER_GAMES_ARROW_SCHEMA
    ]


def _validation_manifest(audit: dict, source_count: int) -> dict:
    rows = audit["player_games"]
    validated = audit["validated_team_games"]
    return {
        "rows": int(len(rows)),
        "games": int(rows["game_id"].nunique()),
        "players": int(rows["person_id"].nunique()),
        "cache_entries": int(source_count),
        "coverage": {
            "status": "passed",
            "team_games": int(len(validated)),
            "regular_team_games": int(
                validated["game_type"].eq("regular").sum()
            ),
            "postseason_team_games": int(
                validated["game_type"].isin(["playoff", "play_in"]).sum()
            ),
        },
        "exceptions": {
            section: int(len(audit["discrepancies"][section]))
            for section in ("minutes", "points")
        },
    }


def _recompute_manifest(
    raw_root: Path,
    reference_dir: Path,
    publication_dir: Path,
    *,
    write_partitions: bool,
) -> dict:
    audit, bodies = _audit_from_roots(raw_root, reference_dir)
    partitions = {}
    for season in SEASONS:
        expected_frame = audit["player_games"].loc[
            audit["player_games"]["season"].eq(season)
        ].copy()
        expected = _table_from_frame(expected_frame)
        if write_partitions:
            partition_path = _write_partition(
                expected, publication_dir, season
            )
        else:
            partition_path = (
                publication_dir
                / f"season={season}"
                / "player_games.parquet"
            )
        try:
            reread = _validate_partition(
                expected, partition_path, season=season
            )
        except (OSError, pa.ArrowException) as exc:
            raise PlayerGamesBuildError(
                f"season {season} partition could not be read: {exc}"
            ) from exc
        partitions[str(season)] = _partition_entry(
            reread, partition_path, season
        )

    references = {
        name: _file_binding(reference_dir / name, name)
        for name in REFERENCE_FILENAMES
    }
    manifest = {
        "schema_version": 1,
        "scope": {
            "ending_seasons": list(sorted(SEASONS)),
            "game_types": sorted({
                game_type
                for season in SEASONS
                for game_type in _applicable_game_types(season)
            }),
        },
        "schema": _schema_manifest(),
        "versions": _versions(),
        "sources": {
            "player_logs": sorted(
                (_source_entry(path, raw_root) for path in bodies),
                key=lambda entry: entry["body_path"],
            ),
            "franchise_reference": _file_binding(
                FRANCHISE_REFERENCE_PATH,
                "src/nba_data/reference/franchises.csv",
            ),
        },
        "references": references,
        "exception_ledger": _file_binding(
            EXCEPTIONS_PATH,
            "src/nba_data/reference/player_game_validation_exceptions.json",
        ),
        "partitions": {
            season: partitions[season] for season in sorted(partitions)
        },
        "validation": _validation_manifest(audit, len(bodies)),
    }
    return manifest


def _manifest_bytes(manifest: dict) -> bytes:
    return (
        json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")


def write_source_review(output_dir: Path, audit: dict) -> None:
    """Write deterministic source-only review evidence into a safe directory."""
    output_dir = Path(output_dir)
    try:
        observed = output_dir.lstat()
    except FileNotFoundError:
        observed = None
    if observed is not None:
        if not stat.S_ISDIR(observed.st_mode):
            raise PlayerGamesBuildError(
                f"source-review output is not a real directory: {output_dir}"
            )
        if any(output_dir.iterdir()):
            raise PlayerGamesBuildError(
                f"source-review output directory must be empty: {output_dir}"
            )
    else:
        output_dir.mkdir(parents=True)

    candidates = {"minutes": {}, "points": {}}
    for section in ("minutes", "points"):
        for key, entry in audit["discrepancies"][section].items():
            if section == "points":
                note = (
                    f"Compared player-log total {entry['player_total']} with "
                    f"committed reference total {entry['published_total']}; "
                    "raw source semantics are retained pending human review."
                )
            else:
                note = (
                    f"Compared player-log minute total {entry['player_total']} "
                    f"and opponent total {entry['opponent_total']} with inferred "
                    f"overtime {entry['inferred_overtime_periods']} and committed "
                    f"overtime {entry['published_overtime_periods']}; raw source "
                    "semantics are retained pending human review."
                )
            candidates[section][key] = {**entry, "note": note}

    rows = audit["player_games"]
    groups = rows.groupby(["season", "game_type"], sort=True)
    table_rows = []
    for (season, game_type), frame in groups:
        table_rows.append(
            f"| {int(season)} | {game_type} | {len(frame)} | "
            f"{frame['game_id'].nunique()} | {frame['person_id'].nunique()} |"
        )
    cache_entries = sum(
        len(_applicable_game_types(season)) for season in SEASONS
    )
    point_keys = sorted(candidates["points"])
    minute_keys = sorted(candidates["minutes"])
    markdown = "\n".join([
        "# Player game source audit",
        "",
        f"Scope: ending seasons {sorted(int(value) for value in rows['season'].unique())}; "
        f"game types {sorted(str(value) for value in rows['game_type'].unique())}.",
        "",
        f"Cache entries: {cache_entries}",
        "",
        "Coverage status: passed",
        "",
        "| Ending season | Game type | Rows | Games | Players |",
        "| ---: | --- | ---: | ---: | ---: |",
        *table_rows,
        "",
        "Point candidate keys: " + (", ".join(point_keys) or "(none)"),
        "",
        "Minute candidate keys: " + (", ".join(minute_keys) or "(none)"),
        "",
        "Warning: a missing appearance does not prove roster membership, "
        "eligibility, injury, rest, or expected participation.",
        "",
    ])
    (output_dir / "source-audit.md").write_text(markdown, encoding="utf-8")
    (output_dir / "player_game_validation_exceptions.json").write_bytes(
        _manifest_bytes(candidates)
    )


def build_from_roots(
    raw_root: Path,
    reference_dir: Path,
    stage_dir: Path,
) -> dict:
    """Build a deterministic player-game publication from explicit roots."""
    raw_root = Path(raw_root)
    reference_dir = Path(reference_dir)
    stage_dir = Path(stage_dir)
    _require_empty_stage(stage_dir)
    manifest = _recompute_manifest(
        raw_root, reference_dir, stage_dir, write_partitions=True
    )
    (stage_dir / "_manifest.json").write_bytes(_manifest_bytes(manifest))
    return manifest


def build_tree(data_dir: Path, stage_dir: Path) -> dict:
    """Build from the repository data-directory layout."""
    data_dir = Path(data_dir)
    return build_from_roots(
        data_dir / "raw/stats_nba/player_logs",
        data_dir / "parquet",
        Path(stage_dir),
    )


def _require_exact_publication_layout(publication_dir: Path) -> None:
    try:
        root_info = publication_dir.lstat()
    except FileNotFoundError as exc:
        raise PlayerGamesBuildError(
            f"publication directory does not exist: {publication_dir}"
        ) from exc
    if not stat.S_ISDIR(root_info.st_mode):
        raise PlayerGamesBuildError(
            f"publication root must be a real directory: {publication_dir}"
        )
    expected = {"_manifest.json"} | {
        f"season={season}" for season in SEASONS
    }
    observed = {entry.name for entry in publication_dir.iterdir()}
    if observed != expected:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        raise PlayerGamesBuildError(
            f"publication layout mismatch: missing={missing}, extra={extra}"
        )
    for name in sorted(expected):
        path = publication_dir / name
        info = path.lstat()
        if name == "_manifest.json":
            if not stat.S_ISREG(info.st_mode):
                raise PlayerGamesBuildError(
                    "publication manifest must be a regular file"
                )
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise PlayerGamesBuildError(
                f"publication partition must be a real directory: {name}"
            )
        children = list(path.iterdir())
        if [child.name for child in children] != ["player_games.parquet"]:
            raise PlayerGamesBuildError(
                f"publication partition has unexpected entries: {name}"
            )
        child_info = children[0].lstat()
        if not stat.S_ISREG(child_info.st_mode):
            raise PlayerGamesBuildError(
                f"publication partition file must be regular: {name}"
            )


def _read_publication_manifest(publication_dir: Path) -> dict:
    path = publication_dir / "_manifest.json"
    try:
        payload = path.read_bytes()
        manifest = json.loads(
            payload.decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid numeric constant {value}")
            ),
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PlayerGamesBuildError(
            f"publication manifest is invalid: {path}"
        ) from exc
    if not isinstance(manifest, dict):
        raise PlayerGamesBuildError("publication manifest must be an object")
    if payload != _manifest_bytes(manifest):
        raise PlayerGamesBuildError(
            "publication manifest is not canonical deterministic JSON"
        )
    return manifest


def validate_publication(
    raw_root: Path,
    publication_dir: Path,
    reference_dir: Path,
) -> dict:
    """Independently recompute and validate a complete publication."""
    raw_root = Path(raw_root)
    publication_dir = Path(publication_dir)
    reference_dir = Path(reference_dir)
    _require_exact_publication_layout(publication_dir)
    stored = _read_publication_manifest(publication_dir)
    recomputed = _recompute_manifest(
        raw_root, reference_dir, publication_dir, write_partitions=False
    )
    expected_top = {
        "schema_version", "scope", "schema", "versions", "sources",
        "references", "exception_ledger", "partitions", "validation",
    }
    if set(stored) != expected_top:
        raise PlayerGamesBuildError("manifest top-level keys mismatch")
    checks = (
        ("schema_version", "manifest schema version"),
        ("scope", "manifest scope"),
        ("schema", "manifest schema"),
        ("versions", "manifest versions"),
        ("references", "manifest reference"),
        ("exception_ledger", "manifest exception ledger"),
        ("partitions", "manifest partition"),
        ("validation", "manifest validation"),
    )
    stored_sources = stored.get("sources")
    if (
        not isinstance(stored_sources, dict)
        or set(stored_sources) != {"franchise_reference", "player_logs"}
    ):
        raise PlayerGamesBuildError("manifest source mismatch")
    if (
        stored_sources.get("player_logs")
        != recomputed["sources"]["player_logs"]
    ):
        raise PlayerGamesBuildError("manifest source mismatch")
    if (
        stored_sources.get("franchise_reference")
        != recomputed["sources"]["franchise_reference"]
    ):
        raise PlayerGamesBuildError("manifest franchise reference mismatch")
    for key, label in checks:
        if stored.get(key) != recomputed[key]:
            raise PlayerGamesBuildError(f"{label} mismatch")
    return recomputed


def render_final_audit(
    manifest: dict,
    *,
    manifest_sha256: str,
    snapshot_name: str,
    restore_verified: bool,
) -> str:
    """Render final evidence only for an exact manifest and verified restore."""
    actual = hashlib.sha256(_manifest_bytes(manifest)).hexdigest()
    if manifest_sha256 != actual:
        raise PlayerGamesBuildError(
            f"final manifest hash mismatch: expected {manifest_sha256}, actual {actual}"
        )
    if not snapshot_name or "\n" in snapshot_name:
        raise PlayerGamesBuildError("snapshot name must be non-empty and single-line")
    if restore_verified is not True:
        raise PlayerGamesBuildError("final audit requires a verified restore")
    validation = manifest["validation"]
    return "\n".join([
        "# Player game final audit",
        "",
        f"Manifest SHA-256: `{manifest_sha256}`",
        f"Snapshot: `{snapshot_name}`",
        "Restore verified: yes",
        "",
        f"Rows: {validation['rows']}",
        f"Games: {validation['games']}",
        f"Players: {validation['players']}",
        "",
    ])


def run(data_dir: Path) -> None:
    """Build, reread, validate, and transactionally publish offline."""
    data_dir = Path(data_dir)
    output_dir = data_dir / "parquet-large/player_games"
    staging_root = output_dir.parent
    staging_root.mkdir(parents=True, exist_ok=True)
    stage_dir = Path(
        tempfile.mkdtemp(prefix=".player-games-build-", dir=staging_root)
    )
    stage_info = stage_dir.lstat()
    identity = (stage_info.st_dev, stage_info.st_ino)
    raw_root = data_dir / "raw/stats_nba/player_logs"
    reference_dir = data_dir / "parquet"
    try:
        manifest = build_tree(data_dir, stage_dir)
        reread = validate_publication(raw_root, stage_dir, reference_dir)
        if reread != manifest:
            raise PlayerGamesBuildError(
                "staged player-games publication failed independent reread"
            )
    except Exception:
        try:
            current = stage_dir.lstat()
        except FileNotFoundError:
            current = None
        if (
            current is not None
            and stat.S_ISDIR(current.st_mode)
            and (current.st_dev, current.st_ino) == identity
        ):
            shutil.rmtree(stage_dir)
        raise

    rollback = publication.publish_tree(
        stage_dir,
        output_dir,
        label="player-games publication",
    )
    active = validate_publication(raw_root, output_dir, reference_dir)
    if active != manifest:
        raise PlayerGamesBuildError(
            "active player-games publication failed independent reread"
        )
    if rollback is not None:
        raise PlayerGamesBuildError(
            "player-games publication succeeded but rollback cleanup failed; "
            f"operator cleanup required at {rollback}"
        )
