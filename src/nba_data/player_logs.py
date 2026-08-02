"""Offline normalization for NBA ``LeagueGameLog`` payloads."""

from __future__ import annotations

import json
import math
import numbers
import os
import stat
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests
from nba_api.stats.endpoints import LeagueGameLog
from nba_api.stats.library.http import NBAStatsHTTP, NBAStatsResponse

from nba_data import cache, franchises, stats_nba


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
_INT64_MIN = -(2 ** 63)
_INT64_MAX = 2 ** 63 - 1


class PlayerLogValidationError(ValueError):
    """Raised when a player game-log source response violates its contract."""


def _decode_payload(payload: bytes) -> object:
    """Decode strict JSON, rejecting non-standard numeric constants."""
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON numeric constant: {value}")

    try:
        return json.loads(payload, parse_constant=reject_constant)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as exc:
        raise PlayerLogValidationError(f"invalid player-log JSON: {exc}") from exc


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


def _expected_metadata(season: int, game_type: str) -> dict[str, object]:
    return {
        "provider": "stats_nba",
        "endpoint": "LeagueGameLog",
        "season": season,
        "season_label": season_label(season),
        "game_type": game_type,
        "season_type": SEASON_TYPES[game_type],
        "league_id": "00",
        "player_or_team_abbreviation": "P",
    }


def _read_cached_entry(
    destination: Path, season: int, game_type: str
) -> tuple[bytes, dict] | None:
    attestation = cache.read_attested(destination, validate_payload)
    if attestation is None:
        return None
    payload, metadata = attestation
    expected = _expected_metadata(season, game_type)
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise cache.CacheIntegrityError(
            f"player-log cache metadata mismatch for {destination}"
        )
    if metadata.get("content_length") != len(payload):
        raise cache.CacheIntegrityError(
            f"player-log cache content length mismatch for {destination}"
        )
    return payload, metadata


def read_cached(
    data_dir: Path, season: int, game_type: str
) -> tuple[bytes, dict] | None:
    """Read one stable, attested player-log cache pair."""
    return _read_cached_entry(
        cache_path(Path(data_dir), season, game_type), season, game_type
    )


def validate_cache_inventory_root(raw_root: Path) -> tuple[Path, ...]:
    """Validate the complete, exact player-log cache tree at ``raw_root``."""
    root = Path(raw_root)
    expected_bodies = tuple(
        root / f"season={season}" / f"{game_type}.json"
        for season in SEASONS
        for game_type in applicable_game_types(season)
    )
    expected_relative = {
        path.relative_to(root) for path in expected_bodies
    } | {
        Path(f"{path.relative_to(root)}.meta.json") for path in expected_bodies
    }
    expected_season_dirs = {path.parent.relative_to(root) for path in expected_bodies}

    try:
        root_mode = root.lstat().st_mode
    except FileNotFoundError:
        root_mode = None
    if root_mode is None or not stat.S_ISDIR(root_mode):
        raise cache.CacheIntegrityError(
            f"player-log cache root must be a real directory: {root}"
        )

    observed_relative: set[Path] = set()
    for season_dir in root.iterdir():
        relative_dir = season_dir.relative_to(root)
        if relative_dir not in expected_season_dirs:
            raise cache.CacheIntegrityError(
                f"unexpected player-log cache entry: {relative_dir}"
            )
        if not stat.S_ISDIR(season_dir.lstat().st_mode):
            raise cache.CacheIntegrityError(
                f"unsafe player-log cache entry: {relative_dir}"
            )
        for entry in season_dir.iterdir():
            relative = entry.relative_to(root)
            if relative not in expected_relative:
                raise cache.CacheIntegrityError(
                    f"unexpected player-log cache entry: {relative}"
                )
            if not stat.S_ISREG(entry.lstat().st_mode):
                raise cache.CacheIntegrityError(
                    f"unsafe player-log cache entry: {relative}"
                )
            observed_relative.add(relative)

    if observed_relative != expected_relative:
        missing = sorted(str(path) for path in expected_relative - observed_relative)
        raise cache.CacheIntegrityError(
            f"missing player-log cache entry: {missing}"
        )

    for season in SEASONS:
        for game_type in applicable_game_types(season):
            _read_cached_entry(
                root / f"season={season}" / f"{game_type}.json",
                season,
                game_type,
            )
    return expected_bodies


def validate_cache_inventory(data_dir: Path) -> tuple[Path, ...]:
    """Validate the repository's complete player-log raw-cache inventory."""
    return validate_cache_inventory_root(
        Path(data_dir) / "raw/stats_nba/player_logs"
    )


def available_bytes(path: Path) -> int:
    """Return available filesystem bytes at an existing path."""
    stats = os.statvfs(path)
    return stats.f_bavail * stats.f_frsize


def _nearest_existing_ancestor(path: Path) -> Path:
    current = Path(path)
    while not current.exists():
        parent = current.parent
        if parent == current:
            raise PlayerLogValidationError(
                f"no existing ancestor for player-log cache root {path}"
            )
        current = parent
    return current


def _retry_delay(exc: requests.RequestException, attempt: int) -> float:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status is not None and status != 429 and not 500 <= status <= 599:
        raise cache.SourceAccessError(f"LeagueGameLog request failed: HTTP {status}")
    retry_after = None
    if response is not None:
        header = response.headers.get("Retry-After")
        if header is not None:
            if header.strip().isdigit():
                retry_after = float(header.strip())
                if retry_after > MAX_RETRY_AFTER_S:
                    raise cache.SourceAccessError(
                        "LeagueGameLog request failed: Retry-After "
                        f"{int(retry_after)} exceeds {int(MAX_RETRY_AFTER_S)}"
                    )
    delay = RETRY_DELAYS_S[attempt - 1]
    return max(delay, retry_after) if retry_after is not None else delay


def _request_league_game_log(endpoint: LeagueGameLog, before_request) -> bytes:
    """Send one LeagueGameLog HTTP request without letting nba_api parse errors hide status."""
    url = NBAStatsHTTP.base_url.format(
        endpoint=getattr(endpoint, "endpoint", "leaguegamelog")
    )
    params = sorted(getattr(endpoint, "parameters", {}).items())
    headers = dict(NBAStatsHTTP.headers)
    timeout = getattr(endpoint, "timeout", 90)
    before_request()
    response = requests.get(
        url,
        params=params,
        headers=headers,
        timeout=timeout,
    )
    if response.status_code != 200:
        raise requests.HTTPError(response=response)
    endpoint.nba_response = NBAStatsResponse(
        response.text, response.status_code, response.url
    )
    return endpoint.get_json().encode("utf-8")


def fetch(data_dir: Path, seasons: Iterable[int] = SEASONS) -> None:
    """Fetch uncached LeagueGameLog source pairs in serialized order."""
    data_dir = Path(data_dir)
    ordered_seasons = tuple(dict.fromkeys(seasons))
    raw_root = data_dir / "raw/stats_nba/player_logs"
    capacity_checked = False
    preflight_done = False
    last_request_start: float | None = None

    def request_payload(endpoint: LeagueGameLog) -> bytes:
        nonlocal last_request_start
        def pace_request() -> None:
            nonlocal last_request_start
            now = time.monotonic()
            if last_request_start is not None:
                wait = last_request_start + MIN_REQUEST_INTERVAL_S - now
                if wait > 0:
                    time.sleep(wait)
            last_request_start = time.monotonic()

        return _request_league_game_log(endpoint, pace_request)

    uncached = []
    for season in ordered_seasons:
        for game_type in applicable_game_types(season):
            destination = cache_path(data_dir, season, game_type)
            if read_cached(data_dir, season, game_type) is None:
                uncached.append((season, game_type, destination))

    for season, game_type, destination in uncached:
            if not capacity_checked:
                capacity_path = _nearest_existing_ancestor(raw_root)
                available = available_bytes(capacity_path)
                if available < FETCH_HEADROOM_BYTES:
                    raise PlayerLogValidationError(
                        "insufficient fetch capacity: required "
                        f"{FETCH_HEADROOM_BYTES} bytes, available {available} bytes"
                    )
                capacity_checked = True
            if not preflight_done:
                stats_nba.preflight_dns()
                preflight_done = True

            for attempt in range(1, len(RETRY_DELAYS_S) + 2):
                try:
                    endpoint = LeagueGameLog(
                        league_id="00",
                        player_or_team_abbreviation="P",
                        season=season_label(season),
                        season_type_all_star=SEASON_TYPES[game_type],
                        direction="ASC",
                        sorter="DATE",
                        timeout=90,
                        get_request=False,
                    )
                    payload = request_payload(endpoint)
                except requests.RequestException as exc:
                    if attempt == len(RETRY_DELAYS_S) + 1:
                        raise cache.SourceAccessError(
                            "LeagueGameLog request exhausted retries after "
                            f"{attempt} attempts: {type(exc).__name__}: {exc}"
                        ) from exc
                    time.sleep(_retry_delay(exc, attempt))
                    continue
                cache.publish_bytes(
                    destination,
                    payload,
                    _expected_metadata(season, game_type),
                    validate_payload,
                )
                break


def validate_payload_bytes(payload: bytes) -> None:
    """Validate the source envelope before normalization."""
    document = _decode_payload(payload)
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
    normalized: list[int | None] = []
    for value in values:
        if pd.isna(value):
            if nullable:
                normalized.append(None)
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
            integer = int(value)
        elif not isinstance(value, numbers.Real) or not math.isfinite(value):
            raise PlayerLogValidationError(f"{source_name} must be an exact integer")
        elif isinstance(value, float) and not value.is_integer():
            raise PlayerLogValidationError(f"{source_name} must be an exact integer")
        else:
            integer = int(value)
        if not _INT64_MIN <= integer <= _INT64_MAX:
            raise PlayerLogValidationError(f"{source_name} must be an exact integer")
        normalized.append(integer)

    return pd.Series(normalized, index=values.index, dtype=(
        "Int64" if nullable else "int64"
    ))


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
    document = _decode_payload(payload)
    result_set = document["resultSets"][0]
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
        "matchup_home_abbreviation": matchup_home_abbr,
        "matchup_away_abbreviation": matchup_away_abbr,
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
        matchup_pairs = {
            tuple(sorted((home_abbreviation, away_abbreviation)))
            for home_abbreviation, away_abbreviation in zip(
                group["matchup_home_abbreviation"],
                group["matchup_away_abbreviation"],
            )
        }
        if len(matchup_pairs) != 1:
            raise PlayerLogValidationError(
                f"game {game_id_value} rows disagree on MATCHUP team pair: "
                f"{sorted(matchup_pairs)}"
            )
        identity_columns = [
            "nba_team_id", "team_name_raw", "team_abbreviation_raw",
        ]
        home_identities = group.loc[group["is_home"], identity_columns]
        home_identity_count = len(home_identities.drop_duplicates())
        away_identities = group.loc[~group["is_home"], identity_columns]
        away_identity_count = len(away_identities.drop_duplicates())
        if home_identity_count != 1 or away_identity_count != 1:
            raise PlayerLogValidationError(
                f"game {game_id_value} has {home_identity_count} home team "
                "identities, expected 1"
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
