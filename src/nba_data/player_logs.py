"""Offline normalization for NBA ``LeagueGameLog`` payloads."""

from __future__ import annotations

import json
import math
import numbers
import os
import re
import stat
import time
from collections.abc import Mapping
from datetime import date
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
MATCHUP_EXCEPTIONS_PATH = (
    Path(__file__).resolve().parent
    / "reference/player_game_matchup_exceptions.json"
)
DESIGNATION_EXCEPTIONS_PATH = (
    Path(__file__).resolve().parent
    / "reference/player_game_designation_exceptions.json"
)

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
_MATCHUP_EXCEPTION_FIELDS = {
    "season",
    "game_type",
    "game_id",
    "person_id",
    "game_date",
    "nba_team_id",
    "team_name_raw",
    "team_abbreviation_raw",
    "matchup_raw",
    "expected_home_abbreviation",
    "expected_away_abbreviation",
    "note",
}
_DESIGNATION_EXCEPTION_FIELDS = {
    "season",
    "game_type",
    "game_id",
    "game_date",
    "teams",
    "expected_home_abbreviation",
    "expected_away_abbreviation",
    "expected_home_franchise_id",
    "expected_away_franchise_id",
    "reference_neutral_site",
    "reference_neutral_site_source",
    "note",
}
_DESIGNATION_TEAM_FIELDS = {
    "nba_team_id",
    "team_name_raw",
    "team_abbreviation_raw",
    "matchup_raw",
    "row_count",
}


class PlayerLogValidationError(ValueError):
    """Raised when a player game-log source response violates its contract."""


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise PlayerLogValidationError(
                f"duplicate JSON member name in matchup exceptions: {key!r}"
            )
        result[key] = value
    return result


def parse_matchup_exceptions(payload: bytes) -> dict[str, dict]:
    """Parse the canonical exact matchup-exception ledger."""
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON numeric constant: {value}")

    try:
        document = json.loads(
            payload,
            object_pairs_hook=_unique_json_object,
            parse_constant=reject_constant,
        )
    except PlayerLogValidationError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as exc:
        raise PlayerLogValidationError(
            f"invalid matchup-exception JSON: {exc}"
        ) from exc
    if not isinstance(document, dict):
        raise PlayerLogValidationError("matchup-exception root must be an object")
    if set(document) != {"schema_version", "exceptions"}:
        raise PlayerLogValidationError(
            "matchup-exception top-level keys must be exactly schema_version and exceptions"
        )
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise PlayerLogValidationError(
            "matchup-exception schema_version has wrong type or value"
        )
    exceptions = document["exceptions"]
    if not isinstance(exceptions, dict):
        raise PlayerLogValidationError("matchup exceptions must be an object")

    parsed = {}
    integer_fields = ("season", "person_id", "nba_team_id")
    string_fields = (
        "game_type",
        "game_id",
        "game_date",
        "team_name_raw",
        "team_abbreviation_raw",
        "matchup_raw",
        "expected_home_abbreviation",
        "expected_away_abbreviation",
        "note",
    )
    for key, entry in exceptions.items():
        if not isinstance(key, str) or not re.fullmatch(
            r"[0-9]{4}\|(regular|playoff|play_in)\|[0-9]{10}\|[0-9]+", key
        ):
            raise PlayerLogValidationError(
                f"malformed matchup-exception composite key: {key!r}"
            )
        if not isinstance(entry, dict) or set(entry) != _MATCHUP_EXCEPTION_FIELDS:
            raise PlayerLogValidationError(
                f"matchup-exception entry {key!r} has non-canonical fields"
            )
        for field in integer_fields:
            if type(entry[field]) is not int:
                raise PlayerLogValidationError(
                    f"matchup-exception entry {key!r} field {field!r} has wrong type"
                )
        for field in string_fields:
            if type(entry[field]) is not str:
                raise PlayerLogValidationError(
                    f"matchup-exception entry {key!r} field {field!r} has wrong type"
                )
        if not entry["note"].strip():
            raise PlayerLogValidationError(
                f"matchup-exception entry {key!r} requires a non-empty note"
            )
        for field in (
            "game_type",
            "game_id",
            "game_date",
            "team_name_raw",
            "team_abbreviation_raw",
            "matchup_raw",
            "expected_home_abbreviation",
            "expected_away_abbreviation",
        ):
            if not entry[field]:
                raise PlayerLogValidationError(
                    f"matchup-exception entry {key!r} field {field!r} must not be empty"
                )
        try:
            parsed_date = date.fromisoformat(entry["game_date"])
        except ValueError as exc:
            raise PlayerLogValidationError(
                f"matchup-exception entry {key!r} has invalid game_date"
            ) from exc
        if parsed_date.isoformat() != entry["game_date"]:
            raise PlayerLogValidationError(
                f"matchup-exception entry {key!r} has non-canonical game_date"
            )
        if not 1984 <= entry["season"] <= 2026:
            raise PlayerLogValidationError(
                f"matchup-exception entry {key!r} has season outside 1984..2026"
            )
        if (
            entry["game_type"] not in GAME_TYPES
            or (
                entry["game_type"] == "play_in"
                and entry["season"] < 2020
            )
        ):
            raise PlayerLogValidationError(
                f"matchup-exception entry {key!r} has inapplicable game type"
            )
        if not re.fullmatch(r"[0-9]{10}", entry["game_id"]):
            raise PlayerLogValidationError(
                f"matchup-exception entry {key!r} has invalid game_id"
            )
        if not entry["game_id"].startswith(ID_PREFIXES[entry["game_type"]]):
            raise PlayerLogValidationError(
                f"matchup-exception entry {key!r} has wrong game_id prefix"
            )
        expected_key = (
            f"{entry['season']}|{entry['game_type']}|{entry['game_id']}|"
            f"{entry['person_id']}"
        )
        if key != expected_key:
            raise PlayerLogValidationError(
                f"matchup-exception key/body identity disagreement: {key!r}"
            )
        parsed[key] = dict(entry)
    return parsed


def load_matchup_exceptions(
    path: Path = MATCHUP_EXCEPTIONS_PATH,
) -> dict[str, dict]:
    """Load and parse the packaged matchup-exception ledger."""
    try:
        payload = Path(path).read_bytes()
    except OSError as exc:
        raise PlayerLogValidationError(
            f"matchup-exception ledger could not be read: {path}"
        ) from exc
    return parse_matchup_exceptions(payload)


def parse_designation_exceptions(payload: bytes) -> dict[str, dict]:
    """Parse the canonical exact neutral-game designation ledger."""
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON numeric constant: {value}")

    try:
        document = json.loads(
            payload,
            object_pairs_hook=_unique_json_object,
            parse_constant=reject_constant,
        )
    except PlayerLogValidationError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as exc:
        raise PlayerLogValidationError(
            f"invalid designation-exception JSON: {exc}"
        ) from exc
    if not isinstance(document, dict):
        raise PlayerLogValidationError(
            "designation-exception root must be an object"
        )
    if set(document) != {"schema_version", "designations"}:
        raise PlayerLogValidationError(
            "designation-exception top-level keys must be exactly "
            "schema_version and designations"
        )
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise PlayerLogValidationError(
            "designation-exception schema_version has wrong type or value"
        )
    designations = document["designations"]
    if not isinstance(designations, dict):
        raise PlayerLogValidationError("designations must be an object")

    parsed = {}
    entry_strings = (
        "game_type",
        "game_id",
        "game_date",
        "expected_home_abbreviation",
        "expected_away_abbreviation",
        "expected_home_franchise_id",
        "expected_away_franchise_id",
        "reference_neutral_site_source",
        "note",
    )
    team_strings = (
        "team_name_raw",
        "team_abbreviation_raw",
        "matchup_raw",
    )
    for key, entry in designations.items():
        if not isinstance(key, str) or not re.fullmatch(
            r"[0-9]{4}\|regular\|[0-9]{10}", key
        ):
            raise PlayerLogValidationError(
                f"malformed designation-exception composite key: {key!r}"
            )
        if not isinstance(entry, dict) or set(entry) != _DESIGNATION_EXCEPTION_FIELDS:
            raise PlayerLogValidationError(
                f"designation-exception entry {key!r} has non-canonical fields"
            )
        if type(entry["season"]) is not int:
            raise PlayerLogValidationError(
                f"designation-exception entry {key!r} season has wrong type"
            )
        for field in entry_strings:
            if type(entry[field]) is not str:
                raise PlayerLogValidationError(
                    f"designation-exception entry {key!r} field {field!r} "
                    "has wrong type"
                )
            if field == "note":
                if not entry[field].strip():
                    raise PlayerLogValidationError(
                        f"designation-exception entry {key!r} requires a "
                        "non-empty note"
                    )
            elif not entry[field]:
                raise PlayerLogValidationError(
                    f"designation-exception entry {key!r} field {field!r} "
                    "must not be empty"
                )
        if type(entry["reference_neutral_site"]) is not bool:
            raise PlayerLogValidationError(
                f"designation-exception entry {key!r} neutral flag has wrong type"
            )
        if entry["reference_neutral_site"] is not True:
            raise PlayerLogValidationError(
                f"designation-exception entry {key!r} must be neutral"
            )
        if entry["reference_neutral_site_source"] != "hoopr":
            raise PlayerLogValidationError(
                f"designation-exception entry {key!r} source must be hoopr"
            )
        try:
            parsed_date = date.fromisoformat(entry["game_date"])
        except ValueError as exc:
            raise PlayerLogValidationError(
                f"designation-exception entry {key!r} has invalid date"
            ) from exc
        if parsed_date.isoformat() != entry["game_date"]:
            raise PlayerLogValidationError(
                f"designation-exception entry {key!r} has non-canonical date"
            )
        if not 1984 <= entry["season"] <= 2026:
            raise PlayerLogValidationError(
                f"designation-exception entry {key!r} season is outside 1984..2026"
            )
        if entry["game_type"] != "regular":
            raise PlayerLogValidationError(
                f"designation-exception entry {key!r} must be regular"
            )
        if (
            not re.fullmatch(r"[0-9]{10}", entry["game_id"])
            or not entry["game_id"].startswith(ID_PREFIXES["regular"])
        ):
            raise PlayerLogValidationError(
                f"designation-exception entry {key!r} has invalid game_id"
            )
        expected_key = (
            f"{entry['season']}|{entry['game_type']}|{entry['game_id']}"
        )
        if key != expected_key:
            raise PlayerLogValidationError(
                f"designation-exception key/body identity disagreement: {key!r}"
            )

        teams = entry["teams"]
        if not isinstance(teams, list) or len(teams) != 2:
            raise PlayerLogValidationError(
                f"designation-exception entry {key!r} teams must have 2 items"
            )
        normalized_teams = []
        for team in teams:
            if not isinstance(team, dict) or set(team) != _DESIGNATION_TEAM_FIELDS:
                raise PlayerLogValidationError(
                    f"designation-exception entry {key!r} team has "
                    "non-canonical fields"
                )
            for field in ("nba_team_id", "row_count"):
                if type(team[field]) is not int:
                    raise PlayerLogValidationError(
                        f"designation-exception entry {key!r} team field "
                        f"{field!r} has wrong type"
                    )
                if team[field] <= 0:
                    raise PlayerLogValidationError(
                        f"designation-exception entry {key!r} team field "
                        f"{field!r} must be positive"
                    )
            for field in team_strings:
                if type(team[field]) is not str or not team[field]:
                    raise PlayerLogValidationError(
                        f"designation-exception entry {key!r} team field "
                        f"{field!r} has wrong type or is empty"
                    )
            normalized_teams.append(dict(team))
        team_ids = [team["nba_team_id"] for team in normalized_teams]
        if team_ids != sorted(team_ids):
            raise PlayerLogValidationError(
                f"designation-exception entry {key!r} teams must be sorted"
            )
        if len(set(team_ids)) != 2:
            raise PlayerLogValidationError(
                f"designation-exception entry {key!r} has duplicate teams"
            )
        abbreviations = {
            team["team_abbreviation_raw"] for team in normalized_teams
        }
        expected_abbreviations = {
            entry["expected_home_abbreviation"],
            entry["expected_away_abbreviation"],
        }
        if len(expected_abbreviations) != 2 or expected_abbreviations != abbreviations:
            raise PlayerLogValidationError(
                f"designation-exception entry {key!r} has invalid sides"
            )
        expected_franchises = {
            entry["expected_home_franchise_id"],
            entry["expected_away_franchise_id"],
        }
        if len(expected_franchises) != 2:
            raise PlayerLogValidationError(
                f"designation-exception entry {key!r} has invalid franchises"
            )
        parsed[key] = {**entry, "teams": normalized_teams}
    return parsed


def load_designation_exceptions(
    path: Path = DESIGNATION_EXCEPTIONS_PATH,
) -> dict[str, dict]:
    """Load and parse the packaged neutral-game designation ledger."""
    try:
        payload = Path(path).read_bytes()
    except OSError as exc:
        raise PlayerLogValidationError(
            f"designation-exception ledger could not be read: {path}"
        ) from exc
    return parse_designation_exceptions(payload)


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


def normalize_payload_with_exception_evidence(
    payload: bytes,
    *,
    season: int,
    game_type: str,
    matchup_exceptions: Mapping[str, Mapping[str, object]],
    designation_exceptions: Mapping[str, Mapping[str, object]],
    franchise_resolver: franchises.FranchiseResolver | None = None,
) -> tuple[pd.DataFrame, dict[str, dict], dict[str, dict]]:
    """Normalize a payload and return exact applied exception evidence."""
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

    person_id = _exact_integer_series(raw["PLAYER_ID"], "PLAYER_ID")
    nba_team_id = _exact_integer_series(raw["TEAM_ID"], "TEAM_ID")
    game_date = pd.to_datetime(raw["GAME_DATE"]).dt.date
    if franchise_resolver is None:
        franchise_id = raw["TEAM_NAME"].map(
            lambda name: franchises.resolve(str(name), season)
        )
    else:
        franchise_id = raw["TEAM_NAME"].map(
            lambda name: franchise_resolver.resolve(str(name), season)
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
    valid_matchup = is_home_side ^ is_away_side
    on_neither_side = ~(is_home_side | is_away_side)
    if on_neither_side.any():
        bad = sorted(team_abbr.loc[on_neither_side].unique())
        raise PlayerLogValidationError(
            "TEAM_ABBREVIATION does not appear on exactly one side of its own "
            f"MATCHUP (appears on neither side): {bad[:5]}"
        )

    row_keys = pd.Series(
        [
            f"{season}|{game_type}|{game_id_value}|{person_id_value}"
            for game_id_value, person_id_value in zip(game_id, person_id)
        ],
        index=raw.index,
    )
    approved_subset = {
        key: dict(entry)
        for key, entry in matchup_exceptions.items()
        if entry.get("season") == season and entry.get("game_type") == game_type
    }
    malformed_indexes = raw.index[~valid_matchup].tolist()
    observed_keys = [row_keys.at[index] for index in malformed_indexes]
    if len(observed_keys) != len(set(observed_keys)):
        raise PlayerLogValidationError(
            "approved matchup key must identify exactly one source row"
        )
    unapproved = sorted(set(observed_keys) - set(approved_subset))
    stale = sorted(set(approved_subset) - set(observed_keys))
    if unapproved:
        raise PlayerLogValidationError(
            "TEAM_ABBREVIATION does not appear on exactly one side of its own "
            f"MATCHUP; unapproved malformed rows: {unapproved}"
        )
    if stale:
        raise PlayerLogValidationError(
            f"stale matchup approvals not observed: {stale}"
        )

    evidence = {}
    identity_columns = ["TEAM_ID", "TEAM_NAME", "TEAM_ABBREVIATION"]
    for malformed_index in malformed_indexes:
        key = row_keys.at[malformed_index]
        approval = approved_subset[key]
        exact_source = {
            "season": season,
            "game_type": game_type,
            "game_id": game_id.at[malformed_index],
            "person_id": int(person_id.at[malformed_index]),
            "game_date": game_date.at[malformed_index].isoformat(),
            "nba_team_id": int(nba_team_id.at[malformed_index]),
            "team_name_raw": raw.at[malformed_index, "TEAM_NAME"],
            "team_abbreviation_raw": raw.at[
                malformed_index, "TEAM_ABBREVIATION"
            ],
            "matchup_raw": raw.at[malformed_index, "MATCHUP"],
        }
        drift = {
            field: (approval.get(field), value)
            for field, value in exact_source.items()
            if approval.get(field) != value
        }
        if drift:
            raise PlayerLogValidationError(
                f"matchup approval raw-field drift for {key}: {drift}"
            )

        group_indexes = raw.index[game_id.eq(game_id.at[malformed_index])]
        group_malformed = [
            index for index in group_indexes if not bool(valid_matchup.at[index])
        ]
        if len(group_malformed) != 1:
            raise PlayerLogValidationError(
                f"game {game_id.at[malformed_index]} has {len(group_malformed)} "
                "malformed matchup rows, expected exactly 1"
            )
        identities = {
            tuple(raw.loc[index, identity_columns].tolist())
            for index in group_indexes
        }
        if len(identities) != 2:
            raise PlayerLogValidationError(
                f"game {game_id.at[malformed_index]} has {len(identities)} raw "
                "team identities, expected 2"
            )
        resolved_franchises = {
            franchise_id.at[index] for index in group_indexes
        }
        if len(resolved_franchises) != 2:
            raise PlayerLogValidationError(
                f"game {game_id.at[malformed_index]} has "
                f"{len(resolved_franchises)} resolved franchises, expected 2"
            )
        dates = {game_date.at[index] for index in group_indexes}
        if len(dates) != 1:
            raise PlayerLogValidationError(
                f"game {game_id.at[malformed_index]} has multiple dates"
            )
        valid_indexes = [
            index for index in group_indexes if bool(valid_matchup.at[index])
        ]
        ordered_pairs = {
            (
                str(matchup_home_abbr.at[index]),
                str(matchup_away_abbr.at[index]),
            )
            for index in valid_indexes
        }
        if len(ordered_pairs) != 1:
            raise PlayerLogValidationError(
                f"game {game_id.at[malformed_index]} lacks unanimous valid-row "
                "matchup consensus"
            )
        valid_identities = {
            tuple(raw.loc[index, identity_columns].tolist())
            for index in valid_indexes
        }
        if valid_identities != identities:
            raise PlayerLogValidationError(
                f"game {game_id.at[malformed_index]} lacks valid rows from both teams"
            )
        valid_home_identities = {
            tuple(raw.loc[index, identity_columns].tolist())
            for index in valid_indexes
            if bool(is_home_side.at[index])
        }
        valid_away_identities = {
            tuple(raw.loc[index, identity_columns].tolist())
            for index in valid_indexes
            if bool(is_away_side.at[index])
        }
        if len(valid_home_identities) != 1 or len(valid_away_identities) != 1:
            raise PlayerLogValidationError(
                f"game {game_id.at[malformed_index]} lacks exactly one valid "
                "identity per side"
            )
        malformed_identity = tuple(
            raw.loc[malformed_index, identity_columns].tolist()
        )
        if malformed_identity not in valid_identities:
            raise PlayerLogValidationError(
                f"approved malformed identity for {key} lacks a valid same-team row"
            )
        consensus_home, consensus_away = next(iter(ordered_pairs))
        approved_pair = (
            approval.get("expected_home_abbreviation"),
            approval.get("expected_away_abbreviation"),
        )
        if approved_pair != (consensus_home, consensus_away):
            raise PlayerLogValidationError(
                f"matchup approval expected-pair drift for {key}: "
                f"{approved_pair!r} != {(consensus_home, consensus_away)!r}"
            )
        malformed_team = team_abbr.at[malformed_index]
        if bool(matched_away.at[malformed_index]):
            separator_agrees = malformed_team == consensus_away
        else:
            separator_agrees = malformed_team == consensus_home
        if not separator_agrees:
            raise PlayerLogValidationError(
                f"malformed matchup separator disagrees with consensus for {key}"
            )
        matchup_home_abbr.at[malformed_index] = consensus_home
        matchup_away_abbr.at[malformed_index] = consensus_away
        is_home_side.at[malformed_index] = malformed_team == consensus_home
        is_away_side.at[malformed_index] = malformed_team == consensus_away
        evidence[key] = {
            "approval": approval,
            "consensus": {
                "game_date": next(iter(dates)).isoformat(),
                "home_abbreviation": consensus_home,
                "away_abbreviation": consensus_away,
                "raw_team_identities": len(identities),
                "resolved_franchises": len(resolved_franchises),
                "malformed_rows": len(group_malformed),
                "valid_rows": len(valid_indexes),
                "valid_team_identities": len(valid_identities),
                "valid_home_identities": len(valid_home_identities),
                "valid_away_identities": len(valid_away_identities),
            },
        }

    designation_subset = {
        key: dict(entry)
        for key, entry in designation_exceptions.items()
        if (
            entry.get("season") == season
            and entry.get("game_type") == game_type
            and entry.get("game_id") in set(game_id)
        )
    }
    identity_columns = ["TEAM_ID", "TEAM_NAME", "TEAM_ABBREVIATION"]
    designation_groups = {}
    for game_id_value, group_indexes in game_id.groupby(game_id, sort=False).groups.items():
        identities = {
            tuple(raw.loc[index, identity_columns].tolist())
            for index in group_indexes
        }
        home_identities_before = {
            tuple(raw.loc[index, identity_columns].tolist())
            for index in group_indexes
            if bool(is_home_side.at[index])
        }
        away_identities_before = {
            tuple(raw.loc[index, identity_columns].tolist())
            for index in group_indexes
            if bool(is_away_side.at[index])
        }
        if (
            len(identities) == 2
            and len(home_identities_before) == 0
            and len(away_identities_before) == 2
        ):
            designation_groups[
                f"{season}|{game_type}|{game_id_value}"
            ] = list(group_indexes)

    observed_designations = set(designation_groups)
    unapproved_designations = sorted(
        observed_designations - set(designation_subset)
    )
    stale_designations = sorted(
        set(designation_subset) - observed_designations
    )
    if unapproved_designations:
        raise PlayerLogValidationError(
            "reciprocal-away designation games require exact approval: "
            f"{unapproved_designations}"
        )
    if stale_designations:
        raise PlayerLogValidationError(
            f"stale designation approvals not observed: {stale_designations}"
        )

    designation_evidence = {}
    for key, group_indexes in sorted(designation_groups.items()):
        approval = designation_subset[key]
        game_id_value = str(game_id.at[group_indexes[0]])
        dates = {game_date.at[index] for index in group_indexes}
        identities = {
            tuple(raw.loc[index, identity_columns].tolist())
            for index in group_indexes
        }
        resolved_franchises = {
            str(franchise_id.at[index]) for index in group_indexes
        }
        group_malformed = [
            index for index in group_indexes if not bool(valid_matchup.at[index])
        ]
        if game_type != "regular":
            raise PlayerLogValidationError(
                f"designation approval {key} is not a regular-season game"
            )
        if len(dates) != 1:
            raise PlayerLogValidationError(
                f"designation game {game_id_value} has multiple dates"
            )
        if len(identities) != 2 or len(resolved_franchises) != 2:
            raise PlayerLogValidationError(
                f"designation game {game_id_value} must have exactly two raw "
                "identities and resolved franchises"
            )
        if group_malformed:
            raise PlayerLogValidationError(
                f"designation game {game_id_value} contains malformed rows"
            )
        if not all(bool(matched_away.at[index]) for index in group_indexes):
            raise PlayerLogValidationError(
                f"designation game {game_id_value} must use only @ syntax"
            )
        if not all(
            str(away_extract.at[index, "left"])
            == str(team_abbr.at[index])
            for index in group_indexes
        ):
            raise PlayerLogValidationError(
                f"designation game {game_id_value} requires every team on the "
                "listed away side"
            )

        team_proof = []
        for team_id, team_name, abbreviation in sorted(
            identities, key=lambda value: int(value[0])
        ):
            team_indexes = [
                index
                for index in group_indexes
                if int(nba_team_id.at[index]) == int(team_id)
                and raw.at[index, "TEAM_NAME"] == team_name
                and raw.at[index, "TEAM_ABBREVIATION"] == abbreviation
            ]
            raw_matchups = {
                str(raw.at[index, "MATCHUP"]) for index in team_indexes
            }
            if len(raw_matchups) != 1:
                raise PlayerLogValidationError(
                    f"designation game {game_id_value} identity "
                    f"{abbreviation!r} has multiple raw matchup strings"
                )
            team_proof.append({
                "nba_team_id": int(team_id),
                "team_name_raw": str(team_name),
                "team_abbreviation_raw": str(abbreviation),
                "matchup_raw": next(iter(raw_matchups)),
                "row_count": len(team_indexes),
            })
        if team_proof != approval.get("teams"):
            raise PlayerLogValidationError(
                f"designation approval raw-team drift for {key}"
            )

        exact_source = {
            "season": season,
            "game_type": game_type,
            "game_id": game_id_value,
            "game_date": next(iter(dates)).isoformat(),
        }
        drift = {
            field: (approval.get(field), value)
            for field, value in exact_source.items()
            if approval.get(field) != value
        }
        if drift:
            raise PlayerLogValidationError(
                f"designation approval raw-field drift for {key}: {drift}"
            )

        abbreviations = [
            team["team_abbreviation_raw"] for team in team_proof
        ]
        if len(set(abbreviations)) != 2:
            raise PlayerLogValidationError(
                f"designation game {game_id_value} has duplicate abbreviations"
            )
        expected_raw = {
            abbreviation: f"{abbreviation} @ {next(value for value in abbreviations if value != abbreviation)}"
            for abbreviation in abbreviations
        }
        if any(
            team["matchup_raw"]
            != expected_raw[team["team_abbreviation_raw"]]
            for team in team_proof
        ):
            raise PlayerLogValidationError(
                f"designation game {game_id_value} raw strings are not reciprocal"
            )
        unordered_pairs = {
            tuple(sorted((
                str(matchup_home_abbr.at[index]),
                str(matchup_away_abbr.at[index]),
            )))
            for index in group_indexes
        }
        if len(unordered_pairs) != 1:
            raise PlayerLogValidationError(
                f"designation game {game_id_value} has multiple matchup pairs"
            )

        expected_home = approval.get("expected_home_abbreviation")
        expected_away = approval.get("expected_away_abbreviation")
        if {expected_home, expected_away} != set(abbreviations):
            raise PlayerLogValidationError(
                f"designation approval expected-side drift for {key}"
            )
        abbreviation_franchises = {}
        for abbreviation in abbreviations:
            values = {
                str(franchise_id.at[index])
                for index in group_indexes
                if str(team_abbr.at[index]) == abbreviation
            }
            if len(values) != 1:
                raise PlayerLogValidationError(
                    f"designation game {game_id_value} identity resolves "
                    "inconsistently"
                )
            abbreviation_franchises[abbreviation] = next(iter(values))
        if (
            abbreviation_franchises[expected_home]
            != approval.get("expected_home_franchise_id")
            or abbreviation_franchises[expected_away]
            != approval.get("expected_away_franchise_id")
        ):
            raise PlayerLogValidationError(
                f"designation approval expected-franchise drift for {key}"
            )

        home_identities_before = {
            tuple(raw.loc[index, identity_columns].tolist())
            for index in group_indexes
            if bool(is_home_side.at[index])
        }
        away_identities_before = {
            tuple(raw.loc[index, identity_columns].tolist())
            for index in group_indexes
            if bool(is_away_side.at[index])
        }
        for index in group_indexes:
            is_home_side.at[index] = str(team_abbr.at[index]) == expected_home
            is_away_side.at[index] = str(team_abbr.at[index]) == expected_away
        home_identities_after = {
            tuple(raw.loc[index, identity_columns].tolist())
            for index in group_indexes
            if bool(is_home_side.at[index])
        }
        away_identities_after = {
            tuple(raw.loc[index, identity_columns].tolist())
            for index in group_indexes
            if bool(is_away_side.at[index])
        }
        if len(home_identities_after) != 1 or len(away_identities_after) != 1:
            raise PlayerLogValidationError(
                f"designation game {game_id_value} did not resolve exactly one "
                "identity per side"
            )
        designation_evidence[key] = {
            "approval": approval,
            "raw": {
                "game_date": next(iter(dates)).isoformat(),
                "rows": len(group_indexes),
                "raw_team_identities": len(identities),
                "resolved_franchises": len(resolved_franchises),
                "valid_xor_rows": len(group_indexes) - len(group_malformed),
                "malformed_rows": len(group_malformed),
                "unordered_matchup_pairs": len(unordered_pairs),
                "home_identities_before": len(home_identities_before),
                "away_identities_before": len(away_identities_before),
                "home_identities_after": len(home_identities_after),
                "away_identities_after": len(away_identities_after),
                "teams": team_proof,
            },
        }

    working = pd.DataFrame({
        "game_id": game_id,
        "person_id": person_id,
        "season": season,
        "game_type": game_type,
        "game_date": game_date,
        "player_name": raw["PLAYER_NAME"],
        "nba_team_id": nba_team_id,
        "team_name_raw": raw["TEAM_NAME"],
        "team_abbreviation_raw": raw["TEAM_ABBREVIATION"],
        "matchup_raw": raw["MATCHUP"],
        "matchup_home_abbreviation": matchup_home_abbr,
        "matchup_away_abbreviation": matchup_away_abbr,
        "franchise_id": franchise_id,
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
    return working[PLAYER_GAME_COLUMNS], evidence, designation_evidence


def normalize_payload_with_matchup_evidence(
    payload: bytes,
    *,
    season: int,
    game_type: str,
    matchup_exceptions: Mapping[str, Mapping[str, object]],
    designation_exceptions: Mapping[str, Mapping[str, object]] | None = None,
    franchise_resolver: franchises.FranchiseResolver | None = None,
) -> tuple[pd.DataFrame, dict[str, dict]]:
    """Normalize while preserving the established two-value matchup API."""
    if designation_exceptions is None:
        designation_exceptions = load_designation_exceptions()
    frame, matchup_evidence, _designation_evidence = (
        normalize_payload_with_exception_evidence(
            payload,
            season=season,
            game_type=game_type,
            matchup_exceptions=matchup_exceptions,
            designation_exceptions=designation_exceptions,
            franchise_resolver=franchise_resolver,
        )
    )
    return frame, matchup_evidence


def normalize_payload(
    payload: bytes,
    *,
    season: int,
    game_type: str,
    matchup_exceptions: Mapping[str, Mapping[str, object]] | None = None,
    designation_exceptions: Mapping[str, Mapping[str, object]] | None = None,
    franchise_resolver: franchises.FranchiseResolver | None = None,
) -> pd.DataFrame:
    """Normalize a source-shaped ``LeagueGameLog`` response."""
    if matchup_exceptions is None:
        matchup_exceptions = load_matchup_exceptions()
    if designation_exceptions is None:
        designation_exceptions = load_designation_exceptions()
    frame, _evidence = normalize_payload_with_matchup_evidence(
        payload,
        season=season,
        game_type=game_type,
        matchup_exceptions=matchup_exceptions,
        designation_exceptions=designation_exceptions,
        franchise_resolver=franchise_resolver,
    )
    return frame
