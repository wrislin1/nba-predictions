"""Parse the fixed V3 play-by-play event contract and derive running scores."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
import re

import pandas as pd
import pyarrow as pa


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
            "action key set differs from ACTION_SOURCE_KEYS: "
            + "; ".join(parts)
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
