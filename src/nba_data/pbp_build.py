"""Parse the fixed V3 play-by-play event contract and derive running scores."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from nba_data import cache, pbp, publication


class PBPBuildError(ValueError):
    """Raised when a V3 play-by-play payload violates the fixed contract."""


ACTION_SOURCE_KEYS = frozenset({
    "actionNumber",
    "clock",
    "period",
    "teamId",
    "teamTricode",
    "personId",
    "playerName",
    "playerNameI",
    "xLegacy",
    "yLegacy",
    "shotDistance",
    "shotResult",
    "isFieldGoal",
    "scoreHome",
    "scoreAway",
    "pointsTotal",
    "location",
    "description",
    "actionType",
    "subType",
    "videoAvailable",
    "shotValue",
    "actionId",
})

EVENT_COLUMNS = [
    "game_id",
    "action_id",
    "action_number",
    "season",
    "game_type",
    "clock",
    "clock_seconds",
    "period",
    "team_id",
    "team_tricode",
    "person_id",
    "player_name",
    "player_name_i",
    "x_legacy",
    "y_legacy",
    "shot_distance",
    "shot_result",
    "is_field_goal",
    "shot_value",
    "score_home_raw",
    "score_away_raw",
    "score_home",
    "score_away",
    "points_total",
    "location",
    "description",
    "action_type",
    "sub_type",
    "video_available",
]

PBP_ARROW_SCHEMA = pa.schema([
    pa.field("game_id", pa.large_string(), nullable=False),
    pa.field("action_id", pa.int64(), nullable=False),
    pa.field("action_number", pa.int64(), nullable=False),
    pa.field("season", pa.int64(), nullable=False),
    pa.field("game_type", pa.large_string(), nullable=False),
    pa.field("clock", pa.large_string(), nullable=False),
    pa.field("clock_seconds", pa.float64(), nullable=False),
    pa.field("period", pa.int64(), nullable=False),
    pa.field("team_id", pa.int64()),
    pa.field("team_tricode", pa.large_string()),
    pa.field("person_id", pa.int64()),
    pa.field("player_name", pa.large_string()),
    pa.field("player_name_i", pa.large_string()),
    pa.field("x_legacy", pa.int64()),
    pa.field("y_legacy", pa.int64()),
    pa.field("shot_distance", pa.int64()),
    pa.field("shot_result", pa.large_string()),
    pa.field("is_field_goal", pa.int64()),
    pa.field("shot_value", pa.int64()),
    pa.field("score_home_raw", pa.large_string(), nullable=False),
    pa.field("score_away_raw", pa.large_string(), nullable=False),
    pa.field("score_home", pa.int64(), nullable=False),
    pa.field("score_away", pa.int64(), nullable=False),
    pa.field("points_total", pa.int64()),
    pa.field("location", pa.large_string()),
    pa.field("description", pa.large_string()),
    pa.field("action_type", pa.large_string()),
    pa.field("sub_type", pa.large_string()),
    pa.field("video_available", pa.int64()),
])


CLOCK_PATTERN = re.compile(
    r"^PT(?P<minutes>\d{2})M(?P<seconds>\d{2}(?:\.\d+)?)S$"
)


def parse_clock(clock: str, period: int) -> float:
    if type(clock) is not str or type(period) is not int or period < 1:
        raise PBPBuildError(f"invalid V3 clock {clock!r} for period {period!r}")
    match = CLOCK_PATTERN.fullmatch(clock)
    if match is None:
        raise PBPBuildError(f"invalid V3 clock {clock!r} for period {period}")
    value = (
        int(match.group("minutes")) * 60
        + float(match.group("seconds"))
    )
    maximum = 720.0 if period <= 4 else 300.0
    if value < 0 or value > maximum:
        raise PBPBuildError(f"invalid V3 clock {clock!r} for period {period}")
    return value


_RAW_SCORE_PATTERN = re.compile(r"^\d+$")


def _derive_running_scores_detailed(
    home_values: list, away_values: list
) -> tuple[list[int], list[int], int, int]:
    """Run the score state machine, also reporting sentinel/blank counts."""
    if len(home_values) != len(away_values):
        raise PBPBuildError(
            "invalid raw score pair: home/away sequences differ in length"
        )
    home_out: list[int] = []
    away_out: list[int] = []
    current: tuple[int, int] | None = None
    sentinel_count = 0
    blank_count = 0
    for index, (raw_home, raw_away) in enumerate(
        zip(home_values, away_values)
    ):
        if type(raw_home) is not str or type(raw_away) is not str:
            raise PBPBuildError(
                f"invalid raw score pair at index {index}: "
                f"{raw_home!r}/{raw_away!r}"
            )
        home_blank = raw_home == ""
        away_blank = raw_away == ""
        if home_blank or away_blank:
            if not (home_blank and away_blank) or current is None:
                raise PBPBuildError(
                    f"invalid raw score pair at index {index}: "
                    f"{raw_home!r}/{raw_away!r}"
                )
            blank_count += 1
            home_out.append(current[0])
            away_out.append(current[1])
            continue
        if (
            _RAW_SCORE_PATTERN.fullmatch(raw_home) is None
            or _RAW_SCORE_PATTERN.fullmatch(raw_away) is None
        ):
            raise PBPBuildError(
                f"invalid raw score pair at index {index}: "
                f"{raw_home!r}/{raw_away!r}"
            )
        home_value = int(raw_home)
        away_value = int(raw_away)
        if current is None:
            if (home_value, away_value) != (0, 0):
                raise PBPBuildError(
                    f"invalid raw score pair at index {index}: "
                    f"first score must be 0-0, got {raw_home!r}/{raw_away!r}"
                )
            current = (0, 0)
        elif home_value == 0 and away_value == 0 and current != (0, 0):
            # Historical zero sentinel: a "0"/"0" pair after scoring has
            # started means "unknown", not "reset to 0-0". Carry forward.
            sentinel_count += 1
        else:
            current = (home_value, away_value)
        home_out.append(current[0])
        away_out.append(current[1])
    return home_out, away_out, sentinel_count, blank_count


def derive_running_scores(
    score_home_raw: Iterable[str], score_away_raw: Iterable[str]
) -> tuple[list[int], list[int]]:
    """Turn raw V3 score strings into non-null running home/away scores.

    Paired ``"0"``/``"0"`` values are the true game start only when no
    running score has been established yet; once a nonzero score is
    running, a later ``"0"``/``"0"`` pair is a historical "unknown" sentinel
    and the previous running score is carried forward. Paired blank
    strings carry the running score forward the same way. One-sided
    blanks, non-integer strings, negative values, and a first pair other
    than ``0``-``0`` are all contract violations.
    """
    home_out, away_out, _, _ = _derive_running_scores_detailed(
        list(score_home_raw), list(score_away_raw)
    )
    return home_out, away_out


_INT_SOURCE_KEYS = frozenset({
    "actionNumber",
    "period",
    "teamId",
    "personId",
    "xLegacy",
    "yLegacy",
    "shotDistance",
    "isFieldGoal",
    "pointsTotal",
    "videoAvailable",
    "shotValue",
    "actionId",
})
_STRING_SOURCE_KEYS = ACTION_SOURCE_KEYS - _INT_SOURCE_KEYS
_REQUIRED_STRING_SOURCE_KEYS = frozenset({"clock", "scoreHome", "scoreAway"})

_SOURCE_TO_EVENT_COLUMN = {
    "actionId": "action_id",
    "actionNumber": "action_number",
    "period": "period",
    "clock": "clock",
    "teamId": "team_id",
    "teamTricode": "team_tricode",
    "personId": "person_id",
    "playerName": "player_name",
    "playerNameI": "player_name_i",
    "xLegacy": "x_legacy",
    "yLegacy": "y_legacy",
    "shotDistance": "shot_distance",
    "shotResult": "shot_result",
    "isFieldGoal": "is_field_goal",
    "shotValue": "shot_value",
    "pointsTotal": "points_total",
    "location": "location",
    "description": "description",
    "actionType": "action_type",
    "subType": "sub_type",
    "videoAvailable": "video_available",
}

_EVENT_COLUMN_DTYPES = {
    "game_id": "string",
    "action_id": "int64",
    "action_number": "int64",
    "season": "int64",
    "game_type": "string",
    "clock": "string",
    "clock_seconds": "float64",
    "period": "int64",
    "team_id": "Int64",
    "team_tricode": "string",
    "person_id": "Int64",
    "player_name": "string",
    "player_name_i": "string",
    "x_legacy": "Int64",
    "y_legacy": "Int64",
    "shot_distance": "Int64",
    "shot_result": "string",
    "is_field_goal": "Int64",
    "shot_value": "Int64",
    "score_home_raw": "string",
    "score_away_raw": "string",
    "score_home": "int64",
    "score_away": "int64",
    "points_total": "Int64",
    "location": "string",
    "description": "string",
    "action_type": "string",
    "sub_type": "string",
    "video_available": "Int64",
}

_DERIVED_EVENT_COLUMNS = frozenset({
    "game_id", "season", "game_type", "clock_seconds", "score_home",
    "score_away",
})

# ``_SOURCE_TO_EVENT_COLUMN`` omits ``scoreHome``/``scoreAway`` because those
# two source keys route through the raw-score-preservation path instead of
# the generic per-key copy loop. The manifest's ``action_fields`` report is
# keyed by source (not internal) field name, so the full 23-key mapping and
# its inverse are needed to translate back.
_FULL_SOURCE_TO_EVENT_COLUMN = {
    **_SOURCE_TO_EVENT_COLUMN,
    "scoreHome": "score_home_raw",
    "scoreAway": "score_away_raw",
}
_EVENT_COLUMN_TO_SOURCE_KEY = {
    column: source_key
    for source_key, column in _FULL_SOURCE_TO_EVENT_COLUMN.items()
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PBPBuildError(message)


def _validate_action_keys(action: Mapping[str, object]) -> None:
    keys = set(action)
    missing = ACTION_SOURCE_KEYS - keys
    extra = keys - ACTION_SOURCE_KEYS
    if missing or extra:
        parts = []
        if missing:
            parts.append(f"missing key(s) {sorted(missing)}")
        if extra:
            parts.append(f"unexpected key(s) {sorted(extra)}")
        raise PBPBuildError(
            "V3 action schema drift: action key set differs from "
            "ACTION_SOURCE_KEYS: " + "; ".join(parts)
        )


def _validate_action_value_families(action: Mapping[str, object]) -> None:
    for key in _INT_SOURCE_KEYS:
        value = action[key]
        if type(value) is not int:
            raise PBPBuildError(
                f"action field {key!r} must be a non-boolean integer, "
                f"got {value!r}"
            )
    for key in _STRING_SOURCE_KEYS:
        value = action[key]
        if not (type(value) is str or value is None):
            raise PBPBuildError(
                f"action field {key!r} must be a string or null, "
                f"got {value!r}"
            )
    for key in _REQUIRED_STRING_SOURCE_KEYS:
        if action[key] is None:
            raise PBPBuildError(f"action field {key!r} must not be null")


def parse_game_payload(
    payload: bytes, worklist_row: Mapping[str, object]
) -> tuple[pd.DataFrame, dict]:
    """Parse one fixed V3 play-by-play JSON body into the event contract.

    Validates the payload against the exact, unchanging V3 shape before
    normalizing it: 23 required keys per action, a positive strictly
    increasing ``actionId`` primary key in response order, contiguous
    ``1..max(period)`` coverage, and a terminal score matching the
    worklist row. Returns the sorted, fully typed event frame plus a
    report describing what was found.
    """
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PBPBuildError(f"invalid V3 payload JSON: {error}") from error

    _require(isinstance(document, dict), "V3 payload must contain an object")
    game = document.get("game")
    _require(isinstance(game, dict), "V3 payload missing 'game' object")
    game_id = game.get("gameId")
    _require(
        isinstance(game_id, str) and game_id != "",
        "V3 payload missing 'game.gameId'",
    )
    expected_game_id = worklist_row["game_id"]
    _require(
        game_id == expected_game_id,
        f"V3 payload gameId {game_id!r} does not match worklist "
        f"gameId {expected_game_id!r}",
    )
    actions = game.get("actions")
    _require(
        isinstance(actions, list) and len(actions) > 0,
        "V3 payload 'game.actions' must be a nonempty list",
    )

    season = worklist_row["season"]
    game_type = worklist_row["game_type"]

    rows: list[dict] = []
    previous_action_id: int | None = None
    for action in actions:
        _require(
            isinstance(action, dict), "V3 payload action must be an object"
        )
        _validate_action_keys(action)
        _validate_action_value_families(action)

        action_id = action["actionId"]
        _require(action_id > 0, f"actionId must be positive, got {action_id}")
        if previous_action_id is not None:
            _require(
                action_id != previous_action_id,
                f"duplicate actionId {action_id}",
            )
            _require(
                action_id > previous_action_id,
                f"actionId must strictly increase, got {action_id} after "
                f"{previous_action_id}",
            )
        previous_action_id = action_id

        period = action["period"]
        clock = action["clock"]
        clock_seconds = parse_clock(clock, period)

        row = {
            "game_id": game_id,
            "season": season,
            "game_type": game_type,
            "clock_seconds": clock_seconds,
            "score_home_raw": action["scoreHome"],
            "score_away_raw": action["scoreAway"],
        }
        for source_key, column in _SOURCE_TO_EVENT_COLUMN.items():
            row[column] = action[source_key]
        rows.append(row)

    home_raw = [row["score_home_raw"] for row in rows]
    away_raw = [row["score_away_raw"] for row in rows]
    home_scores, away_scores, sentinel_count, blank_count = (
        _derive_running_scores_detailed(home_raw, away_raw)
    )
    for row, home_score, away_score in zip(
        rows, home_scores, away_scores, strict=True
    ):
        row["score_home"] = home_score
        row["score_away"] = away_score

    frame = pd.DataFrame(rows, columns=EVENT_COLUMNS)

    periods_seen: list[int] = []
    for value in frame["period"]:
        value = int(value)
        if not periods_seen or periods_seen[-1] != value:
            periods_seen.append(value)
    max_period = max(periods_seen) if periods_seen else 0
    _require(
        periods_seen == list(range(1, max_period + 1)),
        f"periods must run 1..max(period) in order, got {periods_seen}",
    )

    terminal_home = int(frame["score_home"].iloc[-1])
    terminal_away = int(frame["score_away"].iloc[-1])
    expected_home = int(worklist_row["home_pts"])
    expected_away = int(worklist_row["away_pts"])
    _require(
        (terminal_home, terminal_away) == (expected_home, expected_away),
        f"terminal score {terminal_home}-{terminal_away} does not match "
        f"worklist {expected_home}-{expected_away}",
    )

    for column, dtype in _EVENT_COLUMN_DTYPES.items():
        frame[column] = frame[column].astype(dtype)

    frame = frame.sort_values(
        ["game_id", "action_id"], kind="stable"
    ).reset_index(drop=True)

    field_reports = {}
    for column in EVENT_COLUMNS:
        if column in _DERIVED_EVENT_COLUMNS:
            continue
        series = frame[column]
        null_count = int(series.isna().sum())
        blank = (
            int((series == "").sum())
            if pd.api.types.is_string_dtype(series)
            else 0
        )
        field_reports[column] = {"null": null_count, "blank": blank}

    report = {
        "events": int(len(frame)),
        "max_period": int(max_period),
        "terminal_scores": {"home": terminal_home, "away": terminal_away},
        "historical_zero_sentinels": int(sentinel_count),
        "blank_score_pairs": int(blank_count),
        "field_reports": field_reports,
    }
    return frame, report


# ---------------------------------------------------------------------------
# Deterministic season-partitioned publication
#
# ``build_tree`` writes one ``events.parquet`` per ending season plus a
# ``_manifest.json`` describing scope, schema, raw-cache provenance,
# dependency versions, per-partition statistics, and a full validation
# report. ``validate_publication`` recomputes the identical report from an
# already-published tree (never trusting the stored manifest) and requires
# it to match exactly. Both share ``_run_build`` so the two can never drift.
# ---------------------------------------------------------------------------


_TUKEY_MIN_GROUP = 4
_EXTREME_EXAMPLES = 3

_BUILD_GATES = (
    "arrow_schema_exact",
    "primary_key_sorted",
    "primary_key_unique",
    "row_season_consistent",
    "worklist_membership_exact",
    "terminal_scores_match",
    "zstd_compression_verified",
    "partition_checksums_verified",
    "manifest_json_canonical",
)


@dataclass(frozen=True)
class AuditEvidence:
    source_spike: Sequence[Mapping[str, object]]
    cross_source_validated: bool
    reproducible_build: bool
    snapshot_checksums_verified: bool
    restore_verified: bool


def _ordered_seasons(seasons: Iterable[int]) -> list[int]:
    ordered = sorted({int(season) for season in seasons})
    if not ordered:
        raise PBPBuildError("build_tree requires at least one season")
    for season in ordered:
        if season not in pbp.SEASONS:
            raise PBPBuildError(
                f"PBP ending season must be 1997..2026: {season}"
            )
    return ordered


def _require_empty_stage(stage_dir: Path) -> None:
    try:
        info = stage_dir.lstat()
    except FileNotFoundError as exc:
        raise PBPBuildError(
            f"stage directory does not exist: {stage_dir}"
        ) from exc
    if not stat.S_ISDIR(info.st_mode):
        raise PBPBuildError(f"stage path is not a directory: {stage_dir}")
    if any(stage_dir.iterdir()):
        raise PBPBuildError(f"stage directory must be empty: {stage_dir}")


def _versions() -> dict:
    return {
        "nba_api": importlib.metadata.version("nba_api"),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "pyarrow": pa.__version__,
        "python": (
            f"{sys.version_info.major}.{sys.version_info.minor}."
            f"{sys.version_info.micro}"
        ),
    }


def _source_entry(body_path: Path, raw_root: Path) -> dict:
    """Independently attest one raw body/sidecar pair for the manifest.

    Recomputes the body SHA-256/length from the file itself (never trusting
    the sidecar's own claim) and rejects a sidecar whose declared checksum
    or content length disagrees with the body it describes.
    """
    sidecar_path = body_path.with_name(body_path.name + ".meta.json")
    body_bytes = body_path.stat().st_size
    body_sha256 = cache.sha256_file(body_path)
    sidecar_bytes = sidecar_path.read_bytes()
    try:
        metadata = json.loads(sidecar_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PBPBuildError(
            f"cache sidecar is not valid JSON: {sidecar_path}"
        ) from exc
    if not isinstance(metadata, dict):
        raise PBPBuildError(
            f"cache sidecar must contain an object: {sidecar_path}"
        )
    if (
        metadata.get("sha256") != body_sha256
        or metadata.get("content_length") != body_bytes
    ):
        raise PBPBuildError(
            f"cache sidecar checksum/length disagrees with the body: "
            f"{body_path}"
        )
    relative_body = "raw/stats_nba/pbp/" + body_path.relative_to(
        raw_root
    ).as_posix()
    relative_sidecar = "raw/stats_nba/pbp/" + sidecar_path.relative_to(
        raw_root
    ).as_posix()
    return {
        "body_path": relative_body,
        "body_sha256": body_sha256,
        "body_content_length": int(body_bytes),
        "sidecar_path": relative_sidecar,
        "sidecar_sha256": hashlib.sha256(sidecar_bytes).hexdigest(),
        "sidecar_content_length": int(len(sidecar_bytes)),
    }


def _applicable_game_types(season: int) -> list[str]:
    types = ["regular", "playoff"]
    if season in pbp.PLAY_IN_SEASONS:
        types.append("play_in")
    return types


def _event_count_distribution(games: list[tuple[str, int]]) -> dict:
    """Summarize one ``(season, game_type)`` group's per-game event counts.

    Percentiles always use ``numpy.percentile(..., method="linear")``.
    Tukey fences/outliers are only reported for groups of at least four
    games; smaller groups report no outliers. The three smallest and three
    largest games (fewer, if the group itself has fewer than three) are
    always listed, ordered by ``event_count, game_id``.
    """
    ordered = sorted(games, key=lambda pair: (pair[1], pair[0]))
    values = [count for _, count in ordered]
    n = len(values)
    q1, median, q3 = (
        float(value)
        for value in np.percentile(values, [25, 50, 75], method="linear")
    )
    if n >= _TUKEY_MIN_GROUP:
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        tukey_fences = {"lower": lower, "upper": upper}
        outliers = [
            game_id for game_id, count in ordered
            if count < lower or count > upper
        ]
    else:
        tukey_fences = None
        outliers = []
    edge = min(_EXTREME_EXAMPLES, n)
    smallest = ordered[:edge]
    largest = ordered[n - edge:]
    return {
        "count": n,
        "min": int(values[0]),
        "p25": q1,
        "p50": median,
        "p75": q3,
        "max": int(values[-1]),
        "tukey_fences": tukey_fences,
        "outliers": outliers,
        "smallest": [
            {"game_id": game_id, "event_count": int(count)}
            for game_id, count in smallest
        ],
        "largest": [
            {"game_id": game_id, "event_count": int(count)}
            for game_id, count in largest
        ],
    }


def _require_zstd_compression(path: Path) -> None:
    metadata = pq.ParquetFile(path).metadata
    for row_group_index in range(metadata.num_row_groups):
        row_group = metadata.row_group(row_group_index)
        for column_index in range(row_group.num_columns):
            column = row_group.column(column_index)
            if column.compression != "ZSTD":
                raise PBPBuildError(
                    f"partition {path} row group {row_group_index} column "
                    f"{column_index} is not ZSTD-compressed: "
                    f"{column.compression}"
                )


def _logical_row_sha256(table: pa.Table) -> str:
    """Hash a partition's logical row content, independent of file layout.

    Removes Arrow schema metadata, combines chunks, hashes the serialized
    ``PBP_ARROW_SCHEMA`` once, then hashes each primary-key-ordered record
    batch of at most 65,536 rows as an unsigned 8-byte big-endian length
    followed by ``batch.serialize().to_pybytes()``. This is a PyArrow
    version-bound serialization contract; the PyArrow version is recorded
    in the manifest.
    """
    table = table.combine_chunks()
    if table.schema.metadata:
        table = table.replace_schema_metadata(None)
    digest = hashlib.sha256()
    digest.update(PBP_ARROW_SCHEMA.serialize().to_pybytes())
    for batch in table.to_batches(max_chunksize=65_536):
        if batch.schema.metadata:
            batch = batch.replace_schema_metadata(None)
        payload = batch.serialize().to_pybytes()
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _summarize_partition(
    table: pa.Table, *, season: int, worklist_by_game: Mapping[str, Mapping]
) -> dict:
    """Recompute one partition's statistics and enforce every row invariant.

    Enforces exact column order, ``(game_id, action_id)`` sort order and
    uniqueness, row-level season consistency, worklist membership (both
    that every partition game is a known worklist game, and that it is
    filed under its true ending-season partition), worklist ``game_type``
    agreement, and terminal-score agreement with the worklist.
    """
    if table.schema.names != EVENT_COLUMNS:
        raise PBPBuildError(
            f"season {season} partition column order does not match "
            "EVENT_COLUMNS"
        )
    n = table.num_rows
    if n == 0:
        raise PBPBuildError(f"season {season} partition has no rows")

    game_ids = table.column("game_id").to_pylist()
    action_ids = table.column("action_id").to_pylist()
    seasons_col = table.column("season").to_pylist()
    game_types_col = table.column("game_type").to_pylist()
    periods_col = table.column("period").to_pylist()
    score_home = table.column("score_home").to_pylist()
    score_away = table.column("score_away").to_pylist()
    score_home_raw = table.column("score_home_raw").to_pylist()
    score_away_raw = table.column("score_away_raw").to_pylist()

    keys = list(zip(game_ids, action_ids))
    if keys != sorted(keys):
        raise PBPBuildError(
            f"season {season} partition rows are not sorted by "
            "(game_id, action_id)"
        )
    if len(set(keys)) != len(keys):
        raise PBPBuildError(
            f"season {season} partition has duplicate (game_id, action_id) "
            "rows"
        )
    if any(value != season for value in seasons_col):
        raise PBPBuildError(
            f"season {season} partition contains rows tagged with a "
            "different season"
        )

    period_counts: dict[int, int] = {}
    for value in periods_col:
        period_counts[int(value)] = period_counts.get(int(value), 0) + 1

    games_report: dict[str, dict] = {}
    start = 0
    for index in range(1, n + 1):
        if index == n or game_ids[index] != game_ids[start]:
            end = index
            game_id = game_ids[start]
            worklist_row = worklist_by_game.get(game_id)
            if worklist_row is None:
                raise PBPBuildError(
                    f"season {season} partition game {game_id} is absent "
                    "from the worklist"
                )
            if int(worklist_row["season"]) != season:
                raise PBPBuildError(
                    f"game {game_id} belongs to ending season "
                    f"{worklist_row['season']}, found in season {season} "
                    "partition"
                )
            game_type = str(worklist_row["game_type"])
            if any(
                game_types_col[i] != game_type for i in range(start, end)
            ):
                raise PBPBuildError(
                    f"game {game_id} game_type column disagrees with the "
                    "worklist"
                )
            home_raw = score_home_raw[start:end]
            away_raw = score_away_raw[start:end]
            _, _, sentinel_count, blank_count = (
                _derive_running_scores_detailed(home_raw, away_raw)
            )
            terminal_home = int(score_home[end - 1])
            terminal_away = int(score_away[end - 1])
            expected_home = int(worklist_row["home_pts"])
            expected_away = int(worklist_row["away_pts"])
            if (terminal_home, terminal_away) != (expected_home, expected_away):
                raise PBPBuildError(
                    f"game {game_id} terminal score "
                    f"{terminal_home}-{terminal_away} does not match "
                    f"worklist {expected_home}-{expected_away}"
                )
            games_report[game_id] = {
                "season": season,
                "game_type": game_type,
                "event_count": int(end - start),
                "max_period": int(max(periods_col[start:end])),
                "terminal_home_score": terminal_home,
                "terminal_away_score": terminal_away,
                "early_zero_sentinels": int(sentinel_count),
                "blank_score_pairs": int(blank_count),
            }
            start = end

    field_reports: dict[str, dict] = {}
    for column in EVENT_COLUMNS:
        if column in _DERIVED_EVENT_COLUMNS:
            continue
        values = table.column(column).to_pylist()
        null_count = sum(1 for value in values if value is None)
        blank_count = sum(1 for value in values if value == "")
        field_reports[column] = {
            "null": int(null_count), "blank": int(blank_count)
        }

    return {
        "rows": int(n),
        "max_period": int(max(periods_col)),
        "period_counts": period_counts,
        "games": games_report,
        "field_reports": field_reports,
    }


def _process_season_partition(
    reread: pa.Table,
    *,
    season: int,
    worklist_by_game: Mapping[str, Mapping],
    partition_path: Path,
) -> tuple[dict, dict]:
    if not reread.schema.equals(PBP_ARROW_SCHEMA, check_metadata=False):
        raise PBPBuildError(
            f"season {season} partition schema does not match "
            "PBP_ARROW_SCHEMA"
        )
    _require_zstd_compression(partition_path)
    summary = _summarize_partition(
        reread, season=season, worklist_by_game=worklist_by_game
    )
    row_sha256 = _logical_row_sha256(reread)
    file_sha256 = cache.sha256_file(partition_path)
    entry = {
        "path": f"season={season}/events.parquet",
        "rows": summary["rows"],
        "games": len(summary["games"]),
        "bytes": int(partition_path.stat().st_size),
        "max_period": summary["max_period"],
        "sha256": file_sha256,
        "row_sha256": row_sha256,
    }
    return entry, summary


def _run_build(raw_root: Path, ordered_seasons: list[int], *, obtain_partition) -> dict:
    """Shared manifest recomputation for both build and validation.

    ``obtain_partition(season, season_worklist_rows)`` returns
    ``(reread_table, partition_path)`` for one ending season: either freshly
    written (``build_tree``) or already published (``validate_publication``).
    Every other manifest field is derived identically in both directions so
    the two paths can never silently drift apart.
    """
    inventory = pbp.validate_cache_tree(
        raw_root, required_seasons=ordered_seasons, require_complete=True
    )
    worklist = inventory.worklist
    worklist_by_game = {
        str(row["game_id"]): {
            "season": int(row["season"]),
            "game_type": str(row["game_type"]),
            "home_pts": int(row["home_pts"]),
            "away_pts": int(row["away_pts"]),
        }
        for _, row in worklist.iterrows()
    }
    sources = {
        "worklists": sorted(
            (
                _source_entry(path, raw_root)
                for path in inventory.worklist_bodies
            ),
            key=lambda entry: entry["body_path"],
        ),
        "games": sorted(
            (
                _source_entry(path, raw_root)
                for path in inventory.game_bodies
            ),
            key=lambda entry: entry["body_path"],
        ),
    }

    partitions: dict[str, dict] = {}
    worklist_counts: dict[str, dict] = {}
    game_counts: dict[str, dict] = {}
    event_counts: dict[str, dict] = {}
    period_counts: dict[str, dict] = {}
    distributions: dict[str, dict] = {}
    games_validation: dict[str, dict] = {}
    field_totals: dict[str, dict] = {}

    for season in ordered_seasons:
        season_rows = worklist.loc[worklist["season"].eq(season)]
        reread, partition_path = obtain_partition(season, season_rows)
        entry, summary = _process_season_partition(
            reread,
            season=season,
            worklist_by_game=worklist_by_game,
            partition_path=partition_path,
        )
        # Every statistic needed from this season's reread table is already
        # captured in ``entry``/``summary`` above. Drop the table itself
        # (the caller's only remaining strong reference) before advancing to
        # the next season, so a prior season's Arrow table is never alive
        # while the next season's games are being parsed/written.
        del reread
        partitions[str(season)] = entry

        applicable = _applicable_game_types(season)
        counted = season_rows["game_type"].value_counts()
        worklist_counts[str(season)] = {
            game_type: int(counted.get(game_type, 0))
            for game_type in applicable
        }
        by_type: dict[str, list[tuple[str, int]]] = {
            game_type: [] for game_type in applicable
        }
        for game_id, record in summary["games"].items():
            by_type[record["game_type"]].append(
                (game_id, record["event_count"])
            )
        game_counts[str(season)] = {
            game_type: len(by_type[game_type]) for game_type in applicable
        }
        event_counts[str(season)] = {
            game_type: sum(count for _, count in by_type[game_type])
            for game_type in applicable
        }
        period_counts[str(season)] = {
            str(period): count
            for period, count in sorted(summary["period_counts"].items())
        }
        season_distributions = {
            game_type: _event_count_distribution(by_type[game_type])
            for game_type in applicable
            if by_type[game_type]
        }
        if season_distributions:
            distributions[str(season)] = season_distributions
        games_validation.update(summary["games"])
        for column, counts in summary["field_reports"].items():
            source_key = _EVENT_COLUMN_TO_SOURCE_KEY[column]
            bucket = field_totals.setdefault(
                source_key, {"null": 0, "blank": 0}
            )
            bucket["null"] += counts["null"]
            bucket["blank"] += counts["blank"]

    if set(str(game_id) for game_id in worklist["game_id"]) != set(
        games_validation
    ):
        raise PBPBuildError(
            "worklist and built partitions disagree on game membership"
        )

    schema_rows = [
        {
            "name": field.name,
            "arrow_type": str(field.type),
            "nullable": bool(field.nullable),
            "origin": (
                "derived" if field.name in _DERIVED_EVENT_COLUMNS
                else "source"
            ),
        }
        for field in PBP_ARROW_SCHEMA
    ]
    action_fields = {
        key: field_totals.get(key, {"null": 0, "blank": 0})
        for key in sorted(ACTION_SOURCE_KEYS)
    }
    gates = {name: True for name in _BUILD_GATES}
    validation = {
        "worklist_counts": worklist_counts,
        "game_counts": game_counts,
        "event_counts": event_counts,
        "period_counts": period_counts,
        "event_count_distributions": distributions,
        "action_fields": action_fields,
        "games": {
            game_id: games_validation[game_id]
            for game_id in sorted(games_validation)
        },
        "gates": gates,
    }
    return {
        "schema_version": 1,
        "scope": {
            "ending_seasons": list(ordered_seasons),
            "game_types": sorted(pbp.GAME_TYPES),
        },
        "schema": schema_rows,
        "sources": sources,
        "versions": _versions(),
        "partitions": {
            season: partitions[season] for season in sorted(partitions)
        },
        "validation": validation,
    }


def build_tree(raw_root: Path, stage_dir: Path, *, seasons: Iterable[int]) -> dict:
    """Write one deterministic ``events.parquet`` per ending season.

    Parses and writes exactly one season at a time, releasing that season's
    frames/tables before the next season's games are parsed. Creates
    ``_manifest.json`` only after every partition has been reread from disk
    and independently revalidated.
    """
    raw_root = Path(raw_root)
    stage_dir = Path(stage_dir)
    ordered_seasons = _ordered_seasons(seasons)
    _require_empty_stage(stage_dir)

    def obtain_partition(season, season_rows):
        frames = []
        for _, row in season_rows.iterrows():
            game_id = str(row["game_id"])
            body_path = raw_root / "games" / str(season) / f"{game_id}.json"
            payload = cache.require_valid(
                body_path,
                lambda path, game_id=game_id: pbp.validate_game_transport(
                    path, game_id=game_id
                ),
            )
            frame, _report = parse_game_payload(payload, row.to_dict())
            frames.append(frame)
        if not frames:
            raise PBPBuildError(
                f"season {season} has no worklist games to build a "
                "partition from"
            )
        season_frame = pd.concat(frames, ignore_index=True)
        season_frame = season_frame.sort_values(
            ["game_id", "action_id"], kind="stable"
        ).reset_index(drop=True)
        table = pa.Table.from_pandas(
            season_frame,
            schema=PBP_ARROW_SCHEMA,
            preserve_index=False,
            safe=True,
        )
        table = table.replace_schema_metadata(None)
        partition_dir = stage_dir / f"season={season}"
        partition_dir.mkdir(parents=True)
        partition_path = partition_dir / "events.parquet"
        pq.write_table(
            table,
            partition_path,
            compression="zstd",
            compression_level=9,
            use_dictionary=False,
            write_statistics=True,
            row_group_size=65_536,
            data_page_version="2.0",
            version="2.6",
        )
        del season_frame, table, frames
        reread = pq.read_table(partition_path)
        return reread, partition_path

    manifest = _run_build(
        raw_root, ordered_seasons, obtain_partition=obtain_partition
    )
    manifest_path = stage_dir / "_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    return manifest


def _read_publication_manifest(publication_dir: Path) -> dict:
    manifest_path = publication_dir / "_manifest.json"
    try:
        info = manifest_path.lstat()
    except FileNotFoundError as exc:
        raise PBPBuildError(
            f"publication is missing _manifest.json: {publication_dir}"
        ) from exc
    if not stat.S_ISREG(info.st_mode):
        raise PBPBuildError(
            f"publication _manifest.json is not a regular file: "
            f"{publication_dir}"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PBPBuildError(
            f"publication manifest is not valid JSON: {publication_dir}"
        ) from exc
    if not isinstance(manifest, dict):
        raise PBPBuildError(
            f"publication manifest must contain an object: {publication_dir}"
        )
    return manifest


def _validated_scope_seasons(manifest: dict) -> list[int]:
    scope = manifest.get("scope")
    if not isinstance(scope, dict):
        raise PBPBuildError("publication manifest scope is invalid")
    ending_seasons = scope.get("ending_seasons")
    if (
        not isinstance(ending_seasons, list)
        or not ending_seasons
        or any(type(value) is not int for value in ending_seasons)
    ):
        raise PBPBuildError(
            "publication manifest scope.ending_seasons is invalid"
        )
    ordered = sorted(ending_seasons)
    if ordered != ending_seasons or len(set(ordered)) != len(ordered):
        raise PBPBuildError(
            "publication manifest scope.ending_seasons must be sorted and "
            "unique"
        )
    return ordered


def _require_exact_publication_layout(
    publication_dir: Path, ordered_seasons: list[int]
) -> None:
    """Walk the publication without following symlinks: exact entries only."""
    try:
        root_entries = sorted(
            entry.name for entry in publication_dir.iterdir()
        )
    except FileNotFoundError as exc:
        raise PBPBuildError(
            f"publication directory does not exist: {publication_dir}"
        ) from exc
    expected_entries = {
        f"season={season}" for season in ordered_seasons
    } | {"_manifest.json"}
    observed_entries = set(root_entries)
    missing_entries = expected_entries - observed_entries
    extra_entries = observed_entries - expected_entries
    if missing_entries:
        # Reported first/distinctly: a missing season partition (or a
        # missing manifest survives past ``_read_publication_manifest``
        # only if it reappears here, e.g. was moved rather than deleted).
        raise PBPBuildError(
            "publication is missing required top-level entries: "
            f"{sorted(missing_entries)}"
        )
    if extra_entries:
        raise PBPBuildError(
            "publication contains unexpected additional top-level "
            f"entries: {sorted(extra_entries)}"
        )
    for name in root_entries:
        path = publication_dir / name
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise PBPBuildError(
                f"publication entry must not be a symlink: {name}"
            )
        if name == "_manifest.json":
            if not stat.S_ISREG(info.st_mode):
                raise PBPBuildError(
                    "publication _manifest.json must be a regular file"
                )
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise PBPBuildError(
                f"publication entry {name} must be a directory"
            )
        children = sorted(child.name for child in path.iterdir())
        if children != ["events.parquet"]:
            raise PBPBuildError(
                f"publication partition {name} must contain exactly "
                "events.parquet"
            )
        file_info = (path / "events.parquet").lstat()
        if stat.S_ISLNK(file_info.st_mode) or not stat.S_ISREG(
            file_info.st_mode
        ):
            raise PBPBuildError(
                f"publication partition {name}/events.parquet must be a "
                "regular file"
            )


def validate_publication(raw_root: Path, publication_dir: Path) -> dict:
    """Independently recompute and require an exact whole-publication report.

    Walks ``publication_dir`` without following symlinks, requires exactly
    one manifest plus one partition per ``scope.ending_seasons`` season,
    rereads every Parquet file, recomputes every checksum/statistic, and
    verifies raw-cache source provenance -- never trusting anything the
    stored manifest merely claims. Returns the recomputed validation report.
    """
    raw_root = Path(raw_root)
    publication_dir = Path(publication_dir)
    manifest = _read_publication_manifest(publication_dir)
    ordered_seasons = _validated_scope_seasons(manifest)
    _require_exact_publication_layout(publication_dir, ordered_seasons)

    def obtain_partition(season, _season_rows):
        partition_path = publication_dir / f"season={season}" / "events.parquet"
        reread = pq.read_table(partition_path)
        return reread, partition_path

    recomputed = _run_build(
        raw_root, ordered_seasons, obtain_partition=obtain_partition
    )
    if recomputed["sources"] != manifest.get("sources"):
        raise PBPBuildError("manifest source path/checksum mismatch")
    if recomputed["scope"] != manifest.get("scope"):
        raise PBPBuildError("manifest scope mismatch")
    if recomputed["schema"] != manifest.get("schema"):
        raise PBPBuildError("manifest schema mismatch")

    # Partitions: check byte checksum and row count as their own named
    # invariants before falling back to a generic per-season mismatch, so a
    # corrupted checksum and a corrupted row count are never reported under
    # the same message.
    manifest_partitions = manifest.get("partitions") or {}
    expected_partition_keys = {str(season) for season in ordered_seasons}
    if set(manifest_partitions) != expected_partition_keys:
        raise PBPBuildError(
            "manifest partitions do not list exactly scope.ending_seasons"
        )
    for season in ordered_seasons:
        key = str(season)
        recomputed_entry = recomputed["partitions"][key]
        manifest_entry = manifest_partitions[key]
        if recomputed_entry["sha256"] != manifest_entry.get("sha256"):
            raise PBPBuildError(
                f"partition byte checksum mismatch for season {season}"
            )
        if recomputed_entry["rows"] != manifest_entry.get("rows"):
            raise PBPBuildError(
                f"partition row count mismatch for season {season}"
            )
        if recomputed_entry != manifest_entry:
            raise PBPBuildError(
                f"manifest partition mismatch for season {season}: "
                f"recomputed={recomputed_entry}, manifest={manifest_entry}"
            )

    # Validation: check the per-game report as its own named invariant --
    # in particular ``event_count`` and terminal scores -- before falling
    # back to the generic "validation total" comparison, so a single
    # corrupted game statistic is never reported under the same message as
    # an aggregate count drift.
    recomputed_validation = recomputed["validation"]
    manifest_validation = manifest.get("validation") or {}
    recomputed_games = recomputed_validation.get("games", {})
    manifest_games = manifest_validation.get("games", {})
    if set(recomputed_games) != set(manifest_games):
        raise PBPBuildError("manifest validation games membership mismatch")
    for game_id, recomputed_record in recomputed_games.items():
        manifest_record = manifest_games.get(game_id, {})
        if recomputed_record.get("event_count") != manifest_record.get(
            "event_count"
        ):
            raise PBPBuildError(
                f"per-game event count mismatch for game {game_id}: "
                f"recomputed={recomputed_record.get('event_count')}, "
                f"manifest={manifest_record.get('event_count')}"
            )
        if (
            recomputed_record.get("terminal_home_score")
            != manifest_record.get("terminal_home_score")
            or recomputed_record.get("terminal_away_score")
            != manifest_record.get("terminal_away_score")
        ):
            raise PBPBuildError(
                f"terminal score mismatch for game {game_id}"
            )
        if recomputed_record != manifest_record:
            raise PBPBuildError(
                f"per-game validation mismatch for game {game_id}: "
                f"recomputed={recomputed_record}, manifest={manifest_record}"
            )

    if recomputed_validation != manifest_validation:
        raise PBPBuildError("manifest validation total mismatch")
    return recomputed_validation


def run(data_dir: Path) -> None:
    """Build, validate, and publish the production PBP archive offline.

    Stages the full ``pbp.SEASONS`` build under a sibling temporary
    directory, validates it in place, publishes it transactionally over
    ``data_dir/"parquet-large/pbp"``, and revalidates the active
    publication. A pre-publication failure removes only the exact temporary
    stage this call created; a retained rollback directory after a
    successful publish is reported as a fatal error requiring operator
    cleanup.
    """
    data_dir = Path(data_dir)
    output_dir = data_dir / "parquet-large/pbp"
    staging_root = data_dir / "parquet-large"
    staging_root.mkdir(parents=True, exist_ok=True)
    stage_dir = Path(
        tempfile.mkdtemp(prefix=".pbp-build-", dir=staging_root)
    )
    stage_info = stage_dir.lstat()
    stage_identity = (stage_info.st_dev, stage_info.st_ino)
    raw_root = data_dir / "raw/stats_nba/pbp"

    try:
        manifest = build_tree(raw_root, stage_dir, seasons=pbp.SEASONS)
        recomputed = validate_publication(raw_root, stage_dir)
        if recomputed != manifest["validation"]:
            raise PBPBuildError(
                "pbp staged publication failed pre-publish revalidation"
            )
    except Exception:
        try:
            current = stage_dir.lstat()
        except FileNotFoundError:
            current = None
        if (
            current is not None
            and stat.S_ISDIR(current.st_mode)
            and (current.st_dev, current.st_ino) == stage_identity
        ):
            shutil.rmtree(stage_dir)
        raise

    rollback = publication.publish_tree(stage_dir, output_dir, label="pbp")
    post_publish = validate_publication(raw_root, output_dir)
    if post_publish != manifest["validation"]:
        raise PBPBuildError(
            "pbp active publication failed post-publish revalidation"
        )
    if rollback is not None:
        raise PBPBuildError(
            "pbp publication succeeded but the prior tree could not be "
            f"removed; operator cleanup required at {rollback}"
        )


def render_audit(
    manifest: dict,
    evidence: AuditEvidence,
    *,
    manifest_sha256: str,
    snapshot_name: str,
) -> str:
    """Render a Markdown audit, cross-checked against the manifest.

    Rejects a missing/extra source-spike ID or any false
    ``AuditEvidence`` flag before rendering, so the rendered audit can never
    claim an unverified cross-source gate, reproducibility gate, snapshot,
    or restore.
    """
    spike_ids = tuple(pbp.SOURCE_SPIKE_GAME_IDS)
    spike_by_id = {
        str(row["game_id"]): row for row in evidence.source_spike
    }
    if set(spike_by_id) != set(spike_ids):
        raise PBPBuildError(
            "audit evidence must contain exactly the four source-spike IDs: "
            f"{sorted(spike_ids)}"
        )

    validation = manifest["validation"]
    games = validation["games"]
    if not all(validation["gates"].values()):
        raise PBPBuildError("manifest declares a failed build gate")

    game_sources = {}
    for entry in manifest["sources"]["games"]:
        game_id = Path(entry["body_path"]).stem
        game_sources[game_id] = entry

    for game_id in spike_ids:
        row = spike_by_id[game_id]
        record = games.get(game_id)
        source = game_sources.get(game_id)
        if record is None or source is None:
            raise PBPBuildError(
                f"audit evidence spike game is missing from the manifest: "
                f"{game_id}"
            )
        if (
            int(row["response_bytes"]) != int(source["body_content_length"])
            or int(row["event_count"]) != int(record["event_count"])
            or int(row["max_period"]) != int(record["max_period"])
            or int(row["terminal_home_score"])
            != int(record["terminal_home_score"])
            or int(row["terminal_away_score"])
            != int(record["terminal_away_score"])
        ):
            raise PBPBuildError(
                f"audit evidence does not match the manifest for spike "
                f"game {game_id}"
            )

    if not (
        evidence.cross_source_validated
        and evidence.reproducible_build
        and evidence.snapshot_checksums_verified
        and evidence.restore_verified
    ):
        raise PBPBuildError(
            "audit evidence declares an unverified cross-source, "
            "reproducibility, snapshot, or restore gate"
        )

    scope = manifest["scope"]
    lines: list[str] = []
    lines.append("# Play-by-play archive audit")
    lines.append("")
    lines.append(
        f"- Ending-season range: {scope['ending_seasons'][0]}-"
        f"{scope['ending_seasons'][-1]}"
    )
    lines.append(f"- Game types: {', '.join(scope['game_types'])}")
    lines.append(f"- Source schema version: {manifest['schema_version']}")
    lines.append("")

    lines.append("## Package versions")
    for name, value in sorted(manifest["versions"].items()):
        lines.append(f"- {name}: {value}")
    lines.append("")

    total_worklists = sum(
        sum(counts.values())
        for counts in validation["worklist_counts"].values()
    )
    total_games = sum(
        sum(counts.values()) for counts in validation["game_counts"].values()
    )
    total_events = sum(
        sum(counts.values())
        for counts in validation["event_counts"].values()
    )
    lines.append("## Totals")
    lines.append(f"- worklists: {total_worklists}")
    lines.append(f"- games: {total_games}")
    lines.append(f"- events: {total_events}")
    lines.append("")

    lines.append("## Seasons")
    lines.append("| season | games | events | max_period | partition_sha256 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for season in scope["ending_seasons"]:
        key = str(season)
        partition = manifest["partitions"][key]
        season_games = sum(validation["game_counts"].get(key, {}).values())
        season_events = sum(validation["event_counts"].get(key, {}).values())
        lines.append(
            f"| {season} | {season_games} | {season_events} | "
            f"{partition['max_period']} | {partition['sha256']} |"
        )
    lines.append("")

    lines.append("## Anomaly summary")
    for season in scope["ending_seasons"]:
        key = str(season)
        season_distributions = validation["event_count_distributions"].get(
            key, {}
        )
        for game_type in sorted(season_distributions):
            distribution = season_distributions[game_type]
            if distribution["tukey_fences"] is None:
                lines.append(
                    f"- {season}/{game_type}: no Tukey outliers "
                    "(fewer than four games)"
                )
            elif distribution["outliers"]:
                lines.append(
                    f"- {season}/{game_type}: outliers "
                    f"{distribution['outliers']}"
                )
            else:
                lines.append(f"- {season}/{game_type}: no outliers")
    lines.append("")

    lines.append("## Action-field availability")
    for source_key, counts in sorted(validation["action_fields"].items()):
        lines.append(
            f"- {source_key}: null={counts['null']}, blank={counts['blank']}"
        )
    lines.append("")
    early_zero_total = sum(
        record["early_zero_sentinels"] for record in games.values()
    )
    lines.append(f"Early-zero-sentinel total: {early_zero_total}")
    lines.append("")

    lines.append("## Source spikes")
    lines.append(
        "| game_id | response_bytes | event_count | max_period | "
        "terminal_home | terminal_away |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for game_id in spike_ids:
        row = spike_by_id[game_id]
        lines.append(
            f"| {game_id} | {row['response_bytes']} | "
            f"{row['event_count']} | {row['max_period']} | "
            f"{row['terminal_home_score']} | {row['terminal_away_score']} |"
        )
    lines.append("")

    lines.append("## Build gates")
    for name, value in sorted(validation["gates"].items()):
        lines.append(f"- {name}: {'passed' if value else 'FAILED'}")
    lines.append("")

    lines.append("## Execution evidence")
    for name, value in (
        ("cross_source_validated", evidence.cross_source_validated),
        ("reproducible_build", evidence.reproducible_build),
        ("snapshot_checksums_verified", evidence.snapshot_checksums_verified),
        ("restore_verified", evidence.restore_verified),
    ):
        lines.append(f"- {name}: {'passed' if value else 'FAILED'}")
    lines.append("")

    lines.append(f"Full manifest SHA-256: {manifest_sha256}")
    lines.append(f"Immutable snapshot: {snapshot_name}")
    lines.append("Restore verification: passed")
    return "\n".join(lines) + "\n"
