"""Offline normalization for NBA ``LeagueGameLog`` payloads."""

from __future__ import annotations

import json
import math
import numbers
from collections.abc import Mapping
from pathlib import Path

import pandas as pd

from nba_data import franchises


SEASONS = tuple(range(1984, 2027))
PLAY_IN_SEASONS = tuple(range(2020, 2027))
GAME_TYPES = ("regular", "playoff", "play_in")
SEASON_TYPES = {
    "regular": "Regular Season",
    "playoff": "Playoffs",
    "play_in": "PlayIn",
}
ID_PREFIXES = {
    "regular": "002",
    "playoff": "004",
    "play_in": "005",
}
MIN_REQUEST_INTERVAL_S = 1.5
RETRY_DELAYS_S = (5.0, 10.0, 20.0, 40.0)
MAX_RETRY_AFTER_S = 120.0
FETCH_HEADROOM_BYTES = 5_368_709_120

PLAYER_GAME_COLUMNS = [
    "game_id", "person_id", "season", "game_type", "game_date",
    "player_name", "nba_team_id", "team_name_raw",
    "team_abbreviation_raw", "matchup_raw", "franchise_id",
    "opponent_franchise_id", "is_home", "min", "pts", "fgm", "fga",
    "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "reb", "ast",
    "stl", "blk", "tov", "pf", "plus_minus",
]
BOX_COLUMNS = (
    "min", "pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
    "oreb", "dreb", "reb", "ast", "stl", "blk", "tov", "pf",
    "plus_minus",
)
REQUIRED_HEADERS = {
    "SEASON_ID", "PLAYER_ID", "PLAYER_NAME", "TEAM_ID",
    "TEAM_ABBREVIATION", "TEAM_NAME", "GAME_ID", "GAME_DATE",
    "MATCHUP", "WL", "MIN", "FGM", "FGA", "FG_PCT", "FG3M",
    "FG3A", "FG3_PCT", "FTM", "FTA", "FT_PCT", "OREB", "DREB",
    "REB", "AST", "STL", "BLK", "TOV", "PF", "PTS", "PLUS_MINUS",
}

_RESULT_SET_NAME = "LeagueGameLog"


class PlayerLogValidationError(ValueError):
    """Raised when a player game-log source response violates its contract."""


def season_label(season: int) -> str:
    """Return the NBA season label for an ending season."""
    if season not in SEASONS:
        raise PlayerLogValidationError(
            f"player-log ending season must be 1984..2026: {season}"
        )
    return f"{season - 1}-{season % 100:02d}"


def applicable_game_types(season: int) -> tuple[str, ...]:
    """Return the game types expected for an ending season."""
    season_label(season)
    if season in PLAY_IN_SEASONS:
        return GAME_TYPES
    return GAME_TYPES[:2]


def cache_path(data_dir: Path, season: int, game_type: str) -> Path:
    """Return the immutable raw-cache location for one source response."""
    season_label(season)
    if game_type not in applicable_game_types(season):
        raise PlayerLogValidationError(
            f"game type {game_type!r} is not applicable to season {season}"
        )
    return (
        data_dir / "raw/stats_nba/player_logs" / f"season={season}"
        / f"{game_type}.json"
    )


def validate_payload_bytes(payload: bytes) -> None:
    """Validate the source envelope before normalization."""
    try:
        document = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
        raise PlayerLogValidationError(f"invalid player-log JSON: {exc}") from exc
    if not isinstance(document, Mapping):
        raise PlayerLogValidationError("player-log payload must be a JSON object")

    result_sets = document.get("resultSets")
    if not isinstance(result_sets, list):
        raise PlayerLogValidationError("player-log payload missing resultSets")
    matches = [
        entry for entry in result_sets
        if isinstance(entry, Mapping) and entry.get("name") == _RESULT_SET_NAME
    ]
    if len(result_sets) != 1 or len(matches) != 1:
        raise PlayerLogValidationError(
            f"expected exactly one {_RESULT_SET_NAME!r} result set, found {len(matches)}"
        )

    result_set = matches[0]
    headers = result_set.get("headers")
    if not isinstance(headers, list):
        raise PlayerLogValidationError("player-log result set headers must be a list")
    if len(headers) != len(set(headers)):
        raise PlayerLogValidationError("player-log result set has duplicate header")
    if not REQUIRED_HEADERS.issubset(headers):
        missing = sorted(REQUIRED_HEADERS - set(headers))
        raise PlayerLogValidationError(
            f"player-log result set missing required header(s): {missing}"
        )

    row_set = result_set.get("rowSet")
    if not isinstance(row_set, list):
        raise PlayerLogValidationError("player-log result set rowSet must be a list")
    for index, row in enumerate(row_set):
        width = len(row) if isinstance(row, list) else None
        if width != len(headers):
            raise PlayerLogValidationError(
                f"player-log row {index} has width {width!r}, "
                f"expected row width {len(headers)}"
            )


def validate_payload(path: Path) -> None:
    """Validate source bytes stored in a cache file."""
    validate_payload_bytes(path.read_bytes())


def _exact_integer_series(
    values: pd.Series, source_name: str, *, nullable: bool = False
) -> pd.Series:
    """Return source integers while rejecting coercion and truncation."""
    for value in values:
        if pd.isna(value):
            if nullable:
                continue
            raise PlayerLogValidationError(
                f"{source_name} must be an exact integer, not null"
            )
        if isinstance(value, bool):
            raise PlayerLogValidationError(
                f"{source_name} must be an exact integer, not boolean"
            )
        if isinstance(value, str):
            if not value.isdigit():
                raise PlayerLogValidationError(
                    f"{source_name} must be an exact integer"
                )
            continue
        if not isinstance(value, numbers.Real) or not math.isfinite(value):
            raise PlayerLogValidationError(f"{source_name} must be an exact integer")
        if isinstance(value, float) and not value.is_integer():
            raise PlayerLogValidationError(f"{source_name} must be an exact integer")

    numeric = pd.to_numeric(values, errors="raise")
    return numeric.astype("Int64" if nullable else "int64")


def _box_column(values: pd.Series, source_name: str) -> pd.Series:
    """Normalize a nullable integral box-score column exactly."""
    if values.map(lambda value: isinstance(value, bool)).any():
        raise PlayerLogValidationError(f"{source_name} must be an exact integer")
    numeric = pd.to_numeric(values, errors="coerce")
    if (values.notna() & numeric.isna()).any():
        raise PlayerLogValidationError(f"{source_name} must be an exact integer")
    if numeric.notna().any() and not numeric.dropna().mod(1).eq(0).all():
        raise PlayerLogValidationError(f"{source_name} must be an exact integer")
    if source_name != "PLUS_MINUS" and (numeric.dropna() < 0).any():
        raise PlayerLogValidationError(f"{source_name} must be non-negative")
    return numeric.astype("Int64")


def normalize_payload(
    payload: bytes, *, season: int, game_type: str
) -> pd.DataFrame:
    """Normalize a source-shaped ``LeagueGameLog`` response."""
    if game_type not in GAME_TYPES:
        raise PlayerLogValidationError(f"unknown game type: {game_type!r}")
    season_label(season)
    if game_type not in applicable_game_types(season):
        raise PlayerLogValidationError(
            f"game type {game_type!r} is not applicable to season {season}"
        )
    validate_payload_bytes(payload)
    result_set = json.loads(payload)["resultSets"][0]
    raw = pd.DataFrame(result_set["rowSet"], columns=result_set["headers"])

    required_strings = (
        "PLAYER_NAME", "TEAM_ABBREVIATION", "TEAM_NAME", "GAME_DATE", "MATCHUP", "WL",
    )
    for source_name in required_strings:
        if raw[source_name].isna().any():
            raise PlayerLogValidationError(f"{source_name} must not be null")

    season_id = _exact_integer_series(raw["SEASON_ID"], "SEASON_ID")
    expected_digit = ID_PREFIXES[game_type][-1]
    if not raw["SEASON_ID"].astype(str).str.strip().str[0].eq(expected_digit).all():
        raise PlayerLogValidationError(
            "SEASON_ID leading digit does not match requested "
            f"game type {game_type!r}"
        )
    ending_season = (season_id % 10_000) + 1
    if not ending_season.eq(season).all():
        found = sorted(int(value) for value in ending_season.unique())
        raise PlayerLogValidationError(
            f"ending season mismatch: requested {season}, found {found}"
        )

    game_id = _exact_integer_series(
        raw["GAME_ID"], "GAME_ID"
    ).astype(str).str.zfill(10)
    expected_prefix = ID_PREFIXES[game_type]
    if not game_id.str.startswith(expected_prefix).all():
        bad = sorted(
            game_id.loc[~game_id.str.startswith(expected_prefix)].unique()
        )
        raise PlayerLogValidationError(
            f"GAME_ID prefix mismatch for game type {game_type!r}: {bad[:5]}"
        )

    matchup = raw["MATCHUP"].astype(str)
    away_extract = matchup.str.extract(r"^(?P<left>\S+)\s@\s(?P<right>\S+)$")
    home_extract = matchup.str.extract(r"^(?P<left>\S+)\svs\.\s(?P<right>\S+)$")
    matched_away = away_extract["left"].notna()
    matched_home = home_extract["left"].notna()
    if not (matched_away | matched_home).all():
        bad = sorted(matchup.loc[~(matched_away | matched_home)].unique())
        raise PlayerLogValidationError(
            "MATCHUP does not match '<AWAY> @ <HOME>' or "
            f"'<HOME> vs. <AWAY>': {bad[:5]}"
        )
    matchup_home_abbr = away_extract["right"].where(
        matched_away, home_extract["left"]
    )
    matchup_away_abbr = away_extract["left"].where(
        matched_away, home_extract["right"]
    )
    team_abbr = raw["TEAM_ABBREVIATION"].astype(str).str.strip()
    is_home_side = team_abbr.eq(matchup_home_abbr)
    is_away_side = team_abbr.eq(matchup_away_abbr)
    if not (is_home_side ^ is_away_side).all():
        bad = sorted(team_abbr.loc[~(is_home_side ^ is_away_side)].unique())
        raise PlayerLogValidationError(
            "TEAM_ABBREVIATION does not appear on exactly one side of "
            f"its own MATCHUP: {bad[:5]}"
        )

    working = pd.DataFrame({
        "game_id": game_id,
        "person_id": _exact_integer_series(raw["PLAYER_ID"], "PLAYER_ID"),
        "season": season,
        "game_type": game_type,
        "game_date": pd.to_datetime(raw["GAME_DATE"]).dt.date,
        "player_name": raw["PLAYER_NAME"],
        "nba_team_id": _exact_integer_series(raw["TEAM_ID"], "TEAM_ID"),
        "team_name_raw": raw["TEAM_NAME"],
        "team_abbreviation_raw": raw["TEAM_ABBREVIATION"],
        "matchup_raw": raw["MATCHUP"],
        "franchise_id": raw["TEAM_NAME"].map(
            lambda name: franchises.resolve(str(name), season)
        ),
        "is_home": is_home_side,
    })
    for source_name, target_name in {
        "MIN": "min", "PTS": "pts", "FGM": "fgm", "FGA": "fga",
        "FG3M": "fg3m", "FG3A": "fg3a", "FTM": "ftm", "FTA": "fta",
        "OREB": "oreb", "DREB": "dreb", "REB": "reb", "AST": "ast",
        "STL": "stl", "BLK": "blk", "TOV": "tov", "PF": "pf",
        "PLUS_MINUS": "plus_minus",
    }.items():
        working[target_name] = _box_column(raw[source_name], source_name)

    if working.duplicated(["game_id", "person_id"]).any():
        raise PlayerLogValidationError("duplicate (game_id, person_id)")

    opponents: dict[tuple[str, str], str] = {}
    for game_id_value, group in working.groupby("game_id", sort=False):
        identities = group[
            ["nba_team_id", "team_name_raw", "team_abbreviation_raw"]
        ].drop_duplicates()
        if len(identities) != 2:
            raise PlayerLogValidationError(
                f"game {game_id_value} has {len(identities)} team identities, "
                "expected 2"
            )
        franchise_ids = group["franchise_id"].unique().tolist()
        if len(franchise_ids) != 2:
            raise PlayerLogValidationError(
                f"game {game_id_value} has the same resolved franchise "
                f"{franchise_ids[0]!r} on both sides"
            )
        for franchise_id in franchise_ids:
            opponents[(game_id_value, franchise_id)] = next(
                value for value in franchise_ids if value != franchise_id
            )
    working["opponent_franchise_id"] = [
        opponents[(row.game_id, row.franchise_id)]
        for row in working[["game_id", "franchise_id"]].itertuples(index=False)
    ]
    return working[PLAYER_GAME_COLUMNS]
