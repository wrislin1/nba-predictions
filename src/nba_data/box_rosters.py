"""Box-score rosters and scheduled starts from ``stats.nba.com`` (Part A).

The worklist is the distinct game set of the committed ``player_games``
publication, read through its ``_manifest.json``; each game has two raw
identities (``BoxScoreTraditionalV3`` and ``BoxScoreSummaryV3``) cached as
immutable body/sidecar pairs under ``data/raw/stats_nba/box_rosters/``.
"""

from __future__ import annotations

import functools
import hashlib
import io
import json
import re
import stat
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests

from nba_data import cache, pbp, player_logs, stats_nba


PUBLICATION_SEASONS = tuple(range(2012, 2027))
RAW_SUBDIR = "raw/stats_nba/box_rosters"
PLAYER_GAMES_SUBDIR = "parquet-large/player_games"
ENDPOINTS = {
    "traditional": "boxscoretraditionalv3",
    "summary": "boxscoresummaryv3",
}
ENDPOINT_CLASSES = {
    "traditional": "BoxScoreTraditionalV3",
    "summary": "BoxScoreSummaryV3",
}
MIN_REQUEST_INTERVAL_S = 1.5
RETRY_DELAYS_S = (5.0, 10.0, 20.0, 40.0)
MAX_RETRY_AFTER_S = 120.0
FETCH_HEADROOM_BYTES = 5_368_709_120
GAME_CODE_PATTERN = re.compile(r"^\d{8}/[A-Z]{3}[A-Z]{3}$")

GAME_ID_PATTERN = re.compile(r"^\d{10}$")
GAME_TYPES = ("regular", "playoff", "play_in")
PLAYER_GAMES_MANIFEST_NAME = "_manifest.json"
_PLAYER_GAMES_COLUMNS = (
    "game_id", "person_id", "season", "game_type", "game_date",
    "nba_team_id", "franchise_id", "team_abbreviation_raw", "is_home",
)
_TRADITIONAL_PLAYER_STRINGS = (
    "firstName", "familyName", "comment", "jerseyNum", "position",
)
_INACTIVE_STRINGS = ("firstName", "familyName", "jerseyNum")


class BoxRosterValidationError(ValueError):
    """Raised when a box-roster input or identity violates its contract."""


@dataclass(frozen=True)
class WorklistGame:
    game_id: str
    season: int
    game_type: str
    game_date: date


@dataclass(frozen=True)
class PlayerGamesIndex:
    manifest_binding: dict
    games: tuple[WorklistGame, ...]
    appearances: Mapping[str, frozenset[int]]
    team_rows: Mapping[str, Mapping[int, tuple[str, str, bool]]]


# --------------------------------------------------------------------------
# player_games worklist and index
# --------------------------------------------------------------------------


def _require_regular_file(path: Path, label: str) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise BoxRosterValidationError(f"missing {label}: {path}") from None
    if not stat.S_ISREG(info.st_mode):
        raise BoxRosterValidationError(f"{label} must be a regular file: {path}")


def _manifest_partitions(manifest: object) -> dict[int, dict]:
    if not isinstance(manifest, dict):
        raise BoxRosterValidationError(
            "player_games manifest must be a JSON object"
        )
    partitions = manifest.get("partitions")
    if not isinstance(partitions, dict) or not partitions:
        raise BoxRosterValidationError(
            "player_games manifest must carry a non-empty partitions object"
        )
    resolved: dict[int, dict] = {}
    for key, entry in partitions.items():
        if not isinstance(key, str) or not key.isdigit():
            raise BoxRosterValidationError(
                f"player_games partition key must be a season: {key!r}"
            )
        season = int(key)
        if season not in PUBLICATION_SEASONS:
            raise BoxRosterValidationError(
                f"player_games partition season outside 2012..2026: {season}"
            )
        if not isinstance(entry, dict):
            raise BoxRosterValidationError(
                f"player_games partition entry must be an object: {key}"
            )
        path = entry.get("path")
        digest = entry.get("sha256")
        size = entry.get("bytes")
        if not isinstance(path, str) or not path:
            raise BoxRosterValidationError(
                f"player_games partition {key} lacks a path"
            )
        posix = PurePosixPath(path)
        if posix.is_absolute() or ".." in posix.parts:
            raise BoxRosterValidationError(
                f"player_games partition {key} path escapes the root: {path}"
            )
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise BoxRosterValidationError(
                f"player_games partition {key} lacks a SHA-256 digest"
            )
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise BoxRosterValidationError(
                f"player_games partition {key} lacks a byte count"
            )
        rows = entry.get("rows")
        if rows is not None and (
            isinstance(rows, bool) or not isinstance(rows, int) or rows < 0
        ):
            raise BoxRosterValidationError(
                f"player_games partition {key} carries an invalid row count"
            )
        resolved[season] = {
            "path": path, "sha256": digest, "bytes": size, "rows": rows,
        }
    return resolved


def _read_partition(root: Path, season: int, entry: dict) -> pd.DataFrame:
    path = root / entry["path"]
    _require_regular_file(path, f"player_games partition season={season}")
    payload = path.read_bytes()
    if len(payload) != entry["bytes"]:
        raise BoxRosterValidationError(
            f"player_games partition byte count mismatch for season={season}: "
            f"{path}"
        )
    if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
        raise BoxRosterValidationError(
            f"player_games partition SHA-256 mismatch for season={season}: {path}"
        )
    try:
        table = pq.read_table(io.BytesIO(payload), columns=list(_PLAYER_GAMES_COLUMNS))
    except (pa.ArrowException, OSError, ValueError) as exc:
        raise BoxRosterValidationError(
            f"player_games partition could not be read for season={season}: {exc}"
        ) from exc
    if entry["rows"] is not None and table.num_rows != entry["rows"]:
        raise BoxRosterValidationError(
            f"player_games partition row count mismatch for season={season}: "
            f"{table.num_rows} != {entry['rows']}"
        )
    frame = table.to_pandas()
    if frame.empty:
        raise BoxRosterValidationError(
            f"player_games partition is empty for season={season}"
        )
    if frame[list(_PLAYER_GAMES_COLUMNS)].isna().any().any():
        raise BoxRosterValidationError(
            f"player_games partition has null identity columns for season={season}"
        )
    if not frame["season"].eq(season).all():
        raise BoxRosterValidationError(
            f"player_games partition season={season} holds rows of another season"
        )
    if not frame["game_type"].isin(GAME_TYPES).all():
        raise BoxRosterValidationError(
            f"player_games partition season={season} holds an unknown game type"
        )
    if not frame["game_id"].map(
        lambda value: isinstance(value, str) and bool(GAME_ID_PATTERN.match(value))
    ).all():
        raise BoxRosterValidationError(
            f"player_games partition season={season} holds a malformed game id"
        )
    return frame


def load_player_games_index(player_games_root: Path) -> PlayerGamesIndex:
    """Read the ``player_games`` publication into the Part A worklist index."""
    root = Path(player_games_root)
    manifest_path = root / PLAYER_GAMES_MANIFEST_NAME
    _require_regular_file(manifest_path, "player_games manifest")
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BoxRosterValidationError(
            f"player_games manifest is not valid JSON: {manifest_path}"
        ) from exc
    partitions = _manifest_partitions(manifest)
    frames = [
        _read_partition(root, season, partitions[season])
        for season in sorted(partitions)
    ]
    rows = pd.concat(frames, ignore_index=True)

    if rows.duplicated(["game_id", "person_id"]).any():
        raise BoxRosterValidationError(
            "player_games holds a duplicate (game_id, person_id) appearance"
        )

    game_identity = rows[["game_id", "season", "game_type", "game_date"]].drop_duplicates()
    if game_identity["game_id"].duplicated().any():
        duplicates = sorted(
            game_identity.loc[game_identity["game_id"].duplicated(), "game_id"]
        )
        raise BoxRosterValidationError(
            f"player_games game ids with conflicting identity: {duplicates}"
        )
    game_identity = game_identity.sort_values(
        ["season", "game_date", "game_id"], kind="stable"
    )
    games = tuple(
        WorklistGame(
            game_id=str(record.game_id),
            season=int(record.season),
            game_type=str(record.game_type),
            game_date=_as_date(record.game_date),
        )
        for record in game_identity.itertuples(index=False)
    )

    appearances = {
        str(game_id): frozenset(int(value) for value in group)
        for game_id, group in rows.groupby("game_id", sort=True)["person_id"]
    }

    team_identity = rows[
        ["game_id", "nba_team_id", "franchise_id", "team_abbreviation_raw", "is_home"]
    ].drop_duplicates()
    if team_identity.duplicated(["game_id", "nba_team_id"]).any():
        raise BoxRosterValidationError(
            "player_games team rows disagree on franchise, abbreviation or side"
        )
    team_rows: dict[str, dict[int, tuple[str, str, bool]]] = {}
    for record in team_identity.itertuples(index=False):
        per_game = team_rows.setdefault(str(record.game_id), {})
        per_game[int(record.nba_team_id)] = (
            str(record.franchise_id),
            str(record.team_abbreviation_raw),
            bool(record.is_home),
        )
    for game_id, per_game in team_rows.items():
        if len(per_game) > 2:
            raise BoxRosterValidationError(
                f"player_games holds more than two teams for game {game_id}"
            )
        if sum(1 for _, _, is_home in per_game.values() if is_home) > 1:
            raise BoxRosterValidationError(
                f"player_games holds two home teams for game {game_id}"
            )

    manifest_binding = {
        "path": f"{PLAYER_GAMES_SUBDIR}/{PLAYER_GAMES_MANIFEST_NAME}",
        "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "bytes": len(manifest_bytes),
    }
    return PlayerGamesIndex(
        manifest_binding=manifest_binding,
        games=games,
        appearances=appearances,
        team_rows=team_rows,
    )


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, pd.Timestamp):
        return value.date()
    raise BoxRosterValidationError(f"player_games game_date is not a date: {value!r}")


def game_keys(index: PlayerGamesIndex) -> dict[tuple[date, str, str], str]:
    """Map ``(game_date, away abbreviation, home abbreviation)`` to ``game_id``."""
    keys: dict[tuple[date, str, str], str] = {}
    for game in index.games:
        teams = index.team_rows.get(game.game_id, {})
        home = [row for row in teams.values() if row[2]]
        away = [row for row in teams.values() if not row[2]]
        if len(home) != 1 or len(away) != 1:
            raise BoxRosterValidationError(
                f"game {game.game_id} needs exactly one home and away team row"
            )
        key = (game.game_date, away[0][1], home[0][1])
        if key in keys:
            raise BoxRosterValidationError(
                f"games {keys[key]} and {game.game_id} share the key {key}"
            )
        keys[key] = game.game_id
    return keys


# --------------------------------------------------------------------------
# endpoint identities and cache layout
# --------------------------------------------------------------------------


def _require_endpoint(endpoint: str) -> None:
    if endpoint not in ENDPOINTS:
        raise BoxRosterValidationError(
            f"endpoint must be one of {sorted(ENDPOINTS)}: {endpoint!r}"
        )


def _require_game_id(game_id: str) -> None:
    if not isinstance(game_id, str) or not GAME_ID_PATTERN.match(game_id):
        raise BoxRosterValidationError(f"game id must be ten digits: {game_id!r}")


def request_parameters(game_id: str, endpoint: str) -> dict[str, str]:
    """Return the fixed V3 request parameters for one endpoint identity."""
    _require_game_id(game_id)
    _require_endpoint(endpoint)
    if endpoint == "traditional":
        return {
            "GameID": game_id,
            "StartPeriod": "0",
            "EndPeriod": "0",
            "StartRange": "0",
            "EndRange": "0",
            "RangeType": "0",
        }
    return {"GameID": game_id}


def cache_path(data_dir: Path, season: int, game_id: str, endpoint: str) -> Path:
    """Return the immutable raw-cache location for one box-score body."""
    if isinstance(season, bool) or not isinstance(season, int) or (
        season not in PUBLICATION_SEASONS
    ):
        raise BoxRosterValidationError(
            f"ending season must be in 2012..2026: {season!r}"
        )
    _require_game_id(game_id)
    _require_endpoint(endpoint)
    return (
        Path(data_dir) / RAW_SUBDIR / f"season={season}"
        / f"{game_id}.{endpoint}.json"
    )


def expected_metadata(game: WorklistGame, endpoint: str) -> dict:
    """Return the sidecar identity fields bound to one cached body."""
    _require_endpoint(endpoint)
    return {
        "provider": "stats_nba",
        "resource": "box_rosters",
        "endpoint": ENDPOINT_CLASSES[endpoint],
        "parameters": request_parameters(game.game_id, endpoint),
        "season": game.season,
        "game_type": game.game_type,
        "game_id": game.game_id,
    }


# --------------------------------------------------------------------------
# raw validators
# --------------------------------------------------------------------------


def _decode_object(payload: bytes, label: str) -> dict:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON numeric constant: {value}")

    try:
        document = json.loads(payload, parse_constant=reject_constant)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as exc:
        raise BoxRosterValidationError(f"invalid {label} JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise BoxRosterValidationError(f"{label} body must be a JSON object")
    return document


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_int(container: Mapping, key: str, label: str) -> int:
    value = container.get(key)
    if not _is_int(value):
        raise BoxRosterValidationError(f"{label} must carry an integer {key}")
    return value


def _require_str(
    container: Mapping, key: str, label: str, *, non_empty: bool = False
) -> str:
    value = container.get(key)
    if not isinstance(value, str) or (non_empty and not value):
        requirement = "a non-empty string" if non_empty else "a string"
        raise BoxRosterValidationError(f"{label} must carry {requirement} {key}")
    return value


def _require_team_object(root: Mapping, side: str, label: str) -> dict:
    team = root.get(side)
    if not isinstance(team, dict):
        raise BoxRosterValidationError(f"{label} must carry a {side} object")
    team_label = f"{label} {side}"
    _require_int(team, "teamId", team_label)
    _require_str(team, "teamTricode", team_label, non_empty=True)
    _require_str(team, "teamCity", team_label)
    _require_str(team, "teamName", team_label)
    return team


def _require_team_ids_match(root: Mapping, home: Mapping, away: Mapping, label: str) -> None:
    if _require_int(root, "homeTeamId", label) != home["teamId"]:
        raise BoxRosterValidationError(
            f"{label} homeTeamId differs from homeTeam.teamId"
        )
    if _require_int(root, "awayTeamId", label) != away["teamId"]:
        raise BoxRosterValidationError(
            f"{label} awayTeamId differs from awayTeam.teamId"
        )
    if home["teamId"] == away["teamId"]:
        raise BoxRosterValidationError(f"{label} teams share one teamId")


def _require_game_id_match(root: Mapping, game_id: str, label: str) -> None:
    if root.get("gameId") != game_id:
        raise BoxRosterValidationError(
            f"{label} gameId {root.get('gameId')!r} is not the requested {game_id!r}"
        )


def validate_traditional_bytes(payload: bytes, *, game_id: str) -> None:
    """Fail closed unless ``payload`` is a valid ``BoxScoreTraditionalV3`` body."""
    _require_game_id(game_id)
    label = "box-score traditional"
    document = _decode_object(payload, label)
    root = document.get("boxScoreTraditional")
    if not isinstance(root, dict):
        raise BoxRosterValidationError(
            f"{label} body must carry a boxScoreTraditional object"
        )
    _require_game_id_match(root, game_id, label)
    home = _require_team_object(root, "homeTeam", label)
    away = _require_team_object(root, "awayTeam", label)
    _require_team_ids_match(root, home, away, label)
    for side, team in (("homeTeam", home), ("awayTeam", away)):
        players = team.get("players")
        if not isinstance(players, list) or not players:
            raise BoxRosterValidationError(
                f"{label} {side} must carry a non-empty players list"
            )
        for position, player in enumerate(players):
            player_label = f"{label} {side}.players[{position}]"
            if not isinstance(player, dict):
                raise BoxRosterValidationError(f"{player_label} must be an object")
            _require_int(player, "personId", player_label)
            for key in _TRADITIONAL_PLAYER_STRINGS:
                _require_str(player, key, player_label)
            statistics = player.get("statistics")
            if not isinstance(statistics, dict):
                raise BoxRosterValidationError(
                    f"{player_label} must carry a statistics object"
                )
            _require_str(statistics, "minutes", f"{player_label}.statistics")


def _parse_iso_instant(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise BoxRosterValidationError(f"{label} must be an ISO-8601 instant string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise BoxRosterValidationError(
            f"{label} is not an ISO-8601 instant: {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        raise BoxRosterValidationError(
            f"{label} must carry a UTC offset: {value!r}"
        )
    return parsed


def validate_summary_bytes(payload: bytes, *, game_id: str) -> None:
    """Fail closed unless ``payload`` is a valid ``BoxScoreSummaryV3`` body."""
    _require_game_id(game_id)
    label = "box-score summary"
    document = _decode_object(payload, label)
    root = document.get("boxScoreSummary")
    if not isinstance(root, dict):
        raise BoxRosterValidationError(
            f"{label} body must carry a boxScoreSummary object"
        )
    _require_game_id_match(root, game_id, label)
    game_code = root.get("gameCode")
    if not isinstance(game_code, str) or not GAME_CODE_PATTERN.match(game_code):
        raise BoxRosterValidationError(
            f"{label} gameCode must look like YYYYMMDD/AAAHHH: {game_code!r}"
        )
    _parse_iso_instant(root.get("gameTimeUTC"), f"{label} gameTimeUTC")
    if "gameEt" not in root or not isinstance(root["gameEt"], str):
        raise BoxRosterValidationError(f"{label} must carry a gameEt string")
    home = _require_team_object(root, "homeTeam", label)
    away = _require_team_object(root, "awayTeam", label)
    _require_team_ids_match(root, home, away, label)
    for side, team in (("homeTeam", home), ("awayTeam", away)):
        inactives = team.get("inactives")
        if not isinstance(inactives, list):
            raise BoxRosterValidationError(
                f"{label} {side} must carry an inactives list"
            )
        for position, inactive in enumerate(inactives):
            inactive_label = f"{label} {side}.inactives[{position}]"
            if not isinstance(inactive, dict):
                raise BoxRosterValidationError(f"{inactive_label} must be an object")
            _require_int(inactive, "personId", inactive_label)
            for key in _INACTIVE_STRINGS:
                _require_str(inactive, key, inactive_label)


def validate_traditional(path: Path, *, game_id: str) -> None:
    """Path form of :func:`validate_traditional_bytes` for ``cache.publish_bytes``."""
    validate_traditional_bytes(Path(path).read_bytes(), game_id=game_id)


def validate_summary(path: Path, *, game_id: str) -> None:
    """Path form of :func:`validate_summary_bytes` for ``cache.publish_bytes``."""
    validate_summary_bytes(Path(path).read_bytes(), game_id=game_id)


_PATH_VALIDATORS = {
    "traditional": validate_traditional,
    "summary": validate_summary,
}


# --------------------------------------------------------------------------
# cache reads and inventory
# --------------------------------------------------------------------------


def _read_cached_entry(
    destination: Path, game: WorklistGame, endpoint: str
) -> tuple[bytes, dict] | None:
    validator = _PATH_VALIDATORS[endpoint]
    attestation = cache.read_attested(
        destination, lambda path: validator(path, game_id=game.game_id)
    )
    if attestation is None:
        return None
    payload, metadata = attestation
    expected = expected_metadata(game, endpoint)
    if any(
        key not in metadata or metadata[key] != value
        for key, value in expected.items()
    ):
        raise cache.CacheIntegrityError(
            f"box-roster cache metadata mismatch for {destination}"
        )
    if metadata.get("content_length") != len(payload):
        raise cache.CacheIntegrityError(
            f"box-roster cache content length mismatch for {destination}"
        )
    return payload, metadata


def read_cached(
    data_dir: Path, game: WorklistGame, endpoint: str
) -> tuple[bytes, dict] | None:
    """Read one stable, attested box-score cache pair, or ``None`` if absent."""
    destination = cache_path(data_dir, game.season, game.game_id, endpoint)
    return _read_cached_entry(destination, game, endpoint)


def _ordered_games(games: Iterable[WorklistGame]) -> tuple[WorklistGame, ...]:
    ordered = tuple(games)
    seen: set[str] = set()
    for game in ordered:
        if game.game_id in seen:
            raise BoxRosterValidationError(
                f"duplicate worklist game id: {game.game_id}"
            )
        seen.add(game.game_id)
    return ordered


def validate_cache_inventory(
    data_dir: Path, games: Iterable[WorklistGame]
) -> tuple[Path, ...]:
    """Validate the exact box-roster cache tree for ``games`` and return its bodies."""
    data_dir = Path(data_dir)
    root = data_dir / RAW_SUBDIR
    ordered = _ordered_games(games)
    identities = tuple(
        (game, endpoint) for game in ordered for endpoint in ENDPOINTS
    )
    expected_bodies = tuple(
        cache_path(data_dir, game.season, game.game_id, endpoint)
        for game, endpoint in identities
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
            f"box-roster cache root must be a real directory: {root}"
        )

    observed_relative: set[Path] = set()
    for season_dir in sorted(root.iterdir()):
        relative_dir = season_dir.relative_to(root)
        if relative_dir not in expected_season_dirs:
            raise cache.CacheIntegrityError(
                f"unexpected box-roster cache entry: {relative_dir}"
            )
        if not stat.S_ISDIR(season_dir.lstat().st_mode):
            raise cache.CacheIntegrityError(
                f"unsafe box-roster cache entry: {relative_dir}"
            )
        for entry in sorted(season_dir.iterdir()):
            relative = entry.relative_to(root)
            if relative not in expected_relative:
                raise cache.CacheIntegrityError(
                    f"unexpected box-roster cache entry: {relative}"
                )
            if not stat.S_ISREG(entry.lstat().st_mode):
                raise cache.CacheIntegrityError(
                    f"unsafe box-roster cache entry: {relative}"
                )
            observed_relative.add(relative)

    if observed_relative != expected_relative:
        missing = sorted(str(path) for path in expected_relative - observed_relative)
        raise cache.CacheIntegrityError(
            f"missing box-roster cache entry: {missing}"
        )

    for (game, endpoint), destination in zip(identities, expected_bodies):
        if _read_cached_entry(destination, game, endpoint) is None:
            raise cache.CacheIntegrityError(
                f"missing box-roster cache entry: {destination.relative_to(root)}"
            )
    return expected_bodies


# --------------------------------------------------------------------------
# acquisition
# --------------------------------------------------------------------------


def _nearest_existing_ancestor(path: Path) -> Path:
    current = Path(path)
    while not current.exists():
        parent = current.parent
        if parent == current:
            raise BoxRosterValidationError(
                f"no existing ancestor for box-roster cache root {path}"
            )
        current = parent
    return current


def _selected_seasons(seasons: Iterable[int] | None) -> tuple[int, ...]:
    if seasons is None:
        return PUBLICATION_SEASONS
    selected = tuple(dict.fromkeys(seasons))
    for season in selected:
        if isinstance(season, bool) or not isinstance(season, int) or (
            season not in PUBLICATION_SEASONS
        ):
            raise BoxRosterValidationError(
                f"ending season must be in 2012..2026: {season!r}"
            )
    return selected


def fetch(
    data_dir: Path,
    seasons: Iterable[int] | None = None,
    *,
    session: requests.Session | None = None,
    pacer: pbp.RequestPacer | None = None,
    sleeper=time.sleep,
) -> None:
    """Acquire every uncached box-score identity of the ``player_games`` worklist.

    The worklist is the distinct game set of ``data_dir / PLAYER_GAMES_SUBDIR``
    restricted to ``seasons`` (default: every publication season). Each game
    has two identities, ``traditional`` then ``summary``, visited in worklist
    order. A complete valid pair is skipped; a damaged pair raises
    ``CacheIntegrityError`` before any request is sent. Once at least one
    identity is pending, the free-space gate (``FETCH_HEADROOM_BYTES`` at the
    nearest existing ancestor of the raw root) and ``stats_nba.preflight_dns``
    each run exactly once, then each identity is requested through
    ``pbp.request_bytes`` (serial, paced, retried on transient failures) and
    published atomically with its sidecar.

    TODO(task-18): the real acquisition of all 38,366 identities (about 16
    hours of paced requests) runs on the owner's machine in plan Task 18;
    nothing under ``data/raw/stats_nba/box_rosters/`` exists in this checkout.
    """
    data_dir = Path(data_dir)
    selected = _selected_seasons(seasons)
    index = load_player_games_index(data_dir / PLAYER_GAMES_SUBDIR)

    pending: list[tuple[WorklistGame, str, Path]] = []
    for game in index.games:
        if game.season not in selected:
            continue
        for endpoint in ENDPOINTS:
            destination = cache_path(data_dir, game.season, game.game_id, endpoint)
            if _read_cached_entry(destination, game, endpoint) is None:
                pending.append((game, endpoint, destination))
    if not pending:
        return

    raw_root = data_dir / RAW_SUBDIR
    available = player_logs.available_bytes(_nearest_existing_ancestor(raw_root))
    if available < FETCH_HEADROOM_BYTES:
        raise BoxRosterValidationError(
            "insufficient fetch capacity: required "
            f"{FETCH_HEADROOM_BYTES} bytes, available {available} bytes"
        )
    stats_nba.preflight_dns()

    session = session if session is not None else requests.Session()
    pacer = pacer if pacer is not None else pbp.RequestPacer()
    for game, endpoint, destination in pending:
        payload = pbp.request_bytes(
            session=session,
            pacer=pacer,
            endpoint_name=ENDPOINTS[endpoint],
            parameters=request_parameters(game.game_id, endpoint),
            context=f"box_rosters {game.game_id} {endpoint}",
            sleeper=sleeper,
        )
        cache.publish_bytes(
            destination,
            payload,
            expected_metadata(game, endpoint),
            functools.partial(_PATH_VALIDATORS[endpoint], game_id=game.game_id),
        )


# --------------------------------------------------------------------------
# normalisation of one game into roster and schedule rows
# --------------------------------------------------------------------------


GAME_ROSTERS_COLUMNS = (
    "game_id", "person_id", "season", "game_type", "game_date", "nba_team_id",
    "team_tricode_raw", "franchise_id", "is_home", "roster_status",
    "first_name_raw", "family_name_raw", "jersey_num", "starter_position_raw",
    "comment_raw", "comment_code", "minutes_raw",
)
_GAME_ROSTERS_DTYPES = {
    "game_id": "string", "person_id": "Int64", "season": "Int64",
    "game_type": "string", "nba_team_id": "Int64", "team_tricode_raw": "string",
    "franchise_id": "string", "is_home": "boolean", "roster_status": "string",
    "first_name_raw": "string", "family_name_raw": "string",
    "jersey_num": "string", "starter_position_raw": "string",
    "comment_raw": "string", "comment_code": "string", "minutes_raw": "string",
}
COMMENT_SEPARATOR = " - "


@dataclass(frozen=True)
class NormalizedGame:
    rosters: pd.DataFrame
    schedule: dict
    team_strings: dict[int, str]
    tricodes: dict[int, str]
    appearance_disagreements: tuple[dict, ...]
    schedule_disagreement: dict | None
    roster_counts: dict[int, dict[str, int]]


def comment_code(comment: str) -> str:
    """Return the text before the first ``" - "``, or the whole comment."""
    if not isinstance(comment, str):
        raise BoxRosterValidationError(f"comment must be a string: {comment!r}")
    head, separator, _ = comment.partition(COMMENT_SEPARATOR)
    return head if separator else comment


def _null_when_empty(value: str) -> str | None:
    return value if value else None


def _date_prefix(value: str, length: int, *, iso: bool) -> date | None:
    prefix = value[:length]
    try:
        if iso:
            return date.fromisoformat(prefix)
        return datetime.strptime(prefix, "%Y%m%d").date()
    except ValueError:
        return None


def _require_sides_agree(
    traditional_root: Mapping, summary_root: Mapping, game: WorklistGame,
    index: PlayerGamesIndex,
) -> None:
    home_id = traditional_root["homeTeam"]["teamId"]
    away_id = traditional_root["awayTeam"]["teamId"]
    if (
        summary_root["homeTeamId"] != home_id
        or summary_root["awayTeamId"] != away_id
    ):
        raise BoxRosterValidationError(
            f"game {game.game_id}: summary teams "
            f"home={summary_root['homeTeamId']} away={summary_root['awayTeamId']} "
            f"differ from traditional teams home={home_id} away={away_id}"
        )
    team_rows = index.team_rows.get(game.game_id)
    if not team_rows:
        raise BoxRosterValidationError(
            f"game {game.game_id}: no player_games team rows in the index"
        )
    if set(team_rows) != {home_id, away_id}:
        raise BoxRosterValidationError(
            f"game {game.game_id}: body teams {sorted((home_id, away_id))} differ "
            f"from player_games team ids {sorted(team_rows)}"
        )
    for team_id, is_home in ((home_id, True), (away_id, False)):
        if team_rows[team_id][2] != is_home:
            raise BoxRosterValidationError(
                f"game {game.game_id}: team {team_id} is "
                f"{'home' if is_home else 'away'} in the body but "
                f"{'home' if team_rows[team_id][2] else 'away'} in player_games"
            )


def _require_game_code_tricodes(
    game_code: str, traditional_root: Mapping, game_id: str
) -> None:
    code_away, code_home = game_code[9:12], game_code[12:15]
    body_away = traditional_root["awayTeam"]["teamTricode"]
    body_home = traditional_root["homeTeam"]["teamTricode"]
    if (code_away, code_home) != (body_away, body_home):
        raise BoxRosterValidationError(
            f"game {game_id}: gameCode tricodes {code_away}/{code_home} differ "
            f"from the traditional teamTricodes {body_away}/{body_home}"
        )


def normalize_game(
    traditional: bytes,
    summary: bytes,
    *,
    game: WorklistGame,
    index: PlayerGamesIndex,
) -> NormalizedGame:
    """Turn one game's two validated bodies into roster and schedule rows.

    Disagreements between the bodies and ``player_games`` are reported, never
    resolved here: a credited person with a non-empty comment, an empty-comment
    player who is not credited and a credited person with no traditional row
    each become an ``appearance_disagreements`` entry; a ``gameEt`` /
    ``gameCode`` / ``game_date`` date disagreement sets
    ``schedule_disagreement``. Structural conflicts (team ids or sides that
    differ between the bodies and ``player_games``, ``gameCode`` tricodes that
    differ from the traditional body, one person in both an active and an
    inactive list) raise ``BoxRosterValidationError``.
    """
    validate_traditional_bytes(traditional, game_id=game.game_id)
    validate_summary_bytes(summary, game_id=game.game_id)
    traditional_root = json.loads(traditional)["boxScoreTraditional"]
    summary_root = json.loads(summary)["boxScoreSummary"]

    _require_sides_agree(traditional_root, summary_root, game, index)
    game_code = summary_root["gameCode"]
    _require_game_code_tricodes(game_code, traditional_root, game.game_id)
    team_rows = index.team_rows[game.game_id]
    credited = frozenset(index.appearances.get(game.game_id, frozenset()))

    team_strings: dict[int, str] = {}
    tricodes: dict[int, str] = {}
    roster_counts: dict[int, dict[str, int]] = {}
    records: list[dict] = []
    active_people: dict[int, dict] = {}
    inactive_people: dict[int, dict] = {}

    for side, is_home in (("homeTeam", True), ("awayTeam", False)):
        team = traditional_root[side]
        team_id = team["teamId"]
        inactives = summary_root[side]["inactives"]
        team_strings[team_id] = f"{team['teamCity']} {team['teamName']}"
        tricodes[team_id] = team["teamTricode"]
        roster_counts[team_id] = {
            "active": len(team["players"]), "inactive": len(inactives),
        }
        base = {
            "game_id": game.game_id,
            "season": game.season,
            "game_type": game.game_type,
            "game_date": game.game_date,
            "nba_team_id": team_id,
            "team_tricode_raw": team["teamTricode"],
            "franchise_id": team_rows[team_id][0],
            "is_home": is_home,
        }
        for player in team["players"]:
            person_id = player["personId"]
            if person_id in active_people or person_id in inactive_people:
                raise BoxRosterValidationError(
                    f"game {game.game_id}: person {person_id} is listed twice "
                    "(both active lists or an active and an inactive list)"
                )
            comment = _null_when_empty(player["comment"])
            record = {
                **base,
                "person_id": person_id,
                "roster_status": (
                    "played" if person_id in credited else "active_not_played"
                ),
                "first_name_raw": player["firstName"],
                "family_name_raw": player["familyName"],
                "jersey_num": _null_when_empty(player["jerseyNum"].strip()),
                "starter_position_raw": _null_when_empty(player["position"]),
                "comment_raw": comment,
                "comment_code": None if comment is None else comment_code(comment),
                "minutes_raw": _null_when_empty(player["statistics"]["minutes"]),
            }
            active_people[person_id] = record
            records.append(record)
        for inactive in inactives:
            person_id = inactive["personId"]
            if person_id in active_people or person_id in inactive_people:
                raise BoxRosterValidationError(
                    f"game {game.game_id}: person {person_id} is listed twice "
                    "(both inactive lists or an active and an inactive list)"
                )
            record = {
                **base,
                "person_id": person_id,
                "roster_status": "inactive",
                "first_name_raw": inactive["firstName"],
                "family_name_raw": inactive["familyName"],
                "jersey_num": _null_when_empty(inactive["jerseyNum"].strip()),
                "starter_position_raw": None,
                "comment_raw": None,
                "comment_code": None,
                "minutes_raw": None,
            }
            inactive_people[person_id] = record
            records.append(record)

    disagreements: list[dict] = []
    for person_id, record in active_people.items():
        is_credited = person_id in credited
        has_comment = record["comment_raw"] is not None
        if is_credited == has_comment:
            disagreements.append({
                "season": game.season,
                "game_id": game.game_id,
                "person_id": person_id,
                "comment_raw": record["comment_raw"],
                "minutes_raw": record["minutes_raw"],
                "appearance": "credited" if is_credited else "absent",
            })
    for person_id in credited - active_people.keys():
        disagreements.append({
            "season": game.season,
            "game_id": game.game_id,
            "person_id": person_id,
            "comment_raw": None,
            "minutes_raw": None,
            "appearance": "credited",
        })
    disagreements.sort(key=lambda entry: entry["person_id"])

    rosters = pd.DataFrame.from_records(records, columns=list(GAME_ROSTERS_COLUMNS))
    rosters = rosters.astype(_GAME_ROSTERS_DTYPES)
    rosters = rosters.sort_values(
        ["nba_team_id", "roster_status", "person_id"], kind="stable"
    ).reset_index(drop=True)

    game_et_raw = summary_root["gameEt"]
    scheduled_start_utc = _parse_iso_instant(
        summary_root["gameTimeUTC"], "box-score summary gameTimeUTC"
    ).astimezone(timezone.utc)
    schedule = {
        "game_id": game.game_id,
        "season": game.season,
        "game_type": game.game_type,
        "game_date": game.game_date,
        "scheduled_start_utc": scheduled_start_utc,
        "scheduled_start_et_raw": game_et_raw,
        "game_code_raw": game_code,
    }
    et_date = _date_prefix(game_et_raw, 10, iso=True)
    code_date = _date_prefix(game_code, 8, iso=False)
    schedule_disagreement = None
    if not (et_date == code_date == game.game_date):
        schedule_disagreement = {
            "game_id": game.game_id,
            "game_et_raw": game_et_raw,
            "game_code_raw": game_code,
            "game_date": game.game_date,
        }

    return NormalizedGame(
        rosters=rosters,
        schedule=schedule,
        team_strings=team_strings,
        tricodes=tricodes,
        appearance_disagreements=tuple(disagreements),
        schedule_disagreement=schedule_disagreement,
        roster_counts=roster_counts,
    )
