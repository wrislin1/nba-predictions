"""Exact play-by-play worklist construction and cross-source validation.

Offline, network-free normalization of the ``LeagueGameFinderResults``
worklist payload plus bidirectional equality against the published
``games.parquet`` / ``playoff_games.parquet`` tables. Fetch, cache-tree,
and publication interfaces are added by later tasks.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import functools
import json
from pathlib import Path
import stat

import pandas as pd

from nba_data import cache, franchises


class PBPValidationError(ValueError):
    """Raised when a play-by-play worklist or cache violates its contract."""


@dataclass(frozen=True)
class CacheInventory:
    worklist_bodies: Sequence[Path]
    game_bodies: Sequence[Path]
    worklist: pd.DataFrame


SEASONS = tuple(range(1997, 2027))
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
WORKLIST_COLUMNS = [
    "game_id",
    "season",
    "game_type",
    "game_date",
    "away_franchise_id",
    "home_franchise_id",
    "away_pts",
    "home_pts",
]
WORKLIST_REQUIRED_HEADERS = {
    "SEASON_ID",
    "TEAM_ID",
    "TEAM_NAME",
    "TEAM_ABBREVIATION",
    "GAME_ID",
    "GAME_DATE",
    "MATCHUP",
    "WL",
    "PTS",
}
SOURCE_SPIKE_GAME_IDS = (
    "0029600001",
    "0049600001",
    "0051900111",
    "0022500001",
)
MIN_REQUEST_INTERVAL_S = 1.5
RETRY_DELAYS_S = (5.0, 10.0, 20.0, 40.0)
MAX_RETRY_AFTER_S = 120.0
BYTES_PER_UNCACHED_GAME = 1_048_576
FETCH_STAGING_HEADROOM_BYTES = 10_737_418_240


def season_label(season: int) -> str:
    if season not in SEASONS:
        raise ValueError(f"PBP ending season must be 1997..2026: {season}")
    return f"{season - 1}-{season % 100:02d}"


def worklist_path(data_dir: Path, season: int, game_type: str) -> Path:
    return (
        data_dir
        / "raw/stats_nba/pbp/worklists"
        / str(season)
        / f"{game_type}.json"
    )


def game_path(data_dir: Path, season: int, game_id: str) -> Path:
    return (
        data_dir
        / "raw/stats_nba/pbp/games"
        / str(season)
        / f"{game_id}.json"
    )


_RESULT_SET_NAME = "LeagueGameFinderResults"
# SEASON_ID is one season-type digit followed by the four-digit starting
# year (e.g. "21996" for the 1996-97 regular season). This is the source
# encoding of the same regular/playoff/play-in distinction ID_PREFIXES
# expresses over GAME_ID.
_SEASON_ID_DIGITS = {
    game_type: prefix[-1] for game_type, prefix in ID_PREFIXES.items()
}
_REGULAR_KEY = [
    "game_id",
    "season",
    "game_date",
    "away_franchise_id",
    "home_franchise_id",
    "away_pts",
    "home_pts",
]
_POSTSEASON_KEY = [
    "season",
    "game_date",
    "game_type",
    "away_franchise_id",
    "home_franchise_id",
    "away_pts",
    "home_pts",
]
_MISMATCH_SAMPLE_ROWS = 5


def _duplicate_values(series: pd.Series) -> list:
    return sorted(series.loc[series.duplicated()].unique().tolist())


def normalize_worklist_payload(
    payload: bytes, *, season: int, game_type: str
) -> pd.DataFrame:
    """Decode one ``LeagueGameFinderResults`` result set into exact rows.

    Pairs exactly one home and one away team row per game, resolves
    franchises from ``TEAM_NAME`` at the requested ending ``season``, and
    returns rows ordered by ``season, game_date, game_type, game_id``.
    """
    if game_type not in GAME_TYPES:
        raise PBPValidationError(f"unknown game type: {game_type!r}")
    season_label(season)

    try:
        document = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
        raise PBPValidationError(f"invalid worklist JSON: {exc}") from exc
    if not isinstance(document, Mapping):
        raise PBPValidationError("worklist payload must be a JSON object")

    result_sets = document.get("resultSets")
    if not isinstance(result_sets, list):
        raise PBPValidationError("worklist payload missing resultSets")
    matches = [
        entry for entry in result_sets
        if isinstance(entry, Mapping) and entry.get("name") == _RESULT_SET_NAME
    ]
    if len(matches) != 1:
        raise PBPValidationError(
            f"expected exactly one {_RESULT_SET_NAME!r} result set, "
            f"found {len(matches)}"
        )
    result_set = matches[0]

    headers = result_set.get("headers")
    if (
        not isinstance(headers, list)
        or not WORKLIST_REQUIRED_HEADERS.issubset(headers)
    ):
        missing = sorted(WORKLIST_REQUIRED_HEADERS - set(headers or []))
        raise PBPValidationError(
            f"worklist result set missing required header(s): {missing}"
        )

    row_set = result_set.get("rowSet")
    if not isinstance(row_set, list):
        raise PBPValidationError("worklist result set rowSet must be a list")
    for index, row in enumerate(row_set):
        width = len(row) if isinstance(row, list) else None
        if width != len(headers):
            raise PBPValidationError(
                f"worklist row {index} has width {width!r}, "
                f"expected row width {len(headers)}"
            )

    if not row_set:
        return pd.DataFrame(columns=WORKLIST_COLUMNS)

    raw = pd.DataFrame(row_set, columns=headers)

    try:
        season_id_numeric = pd.to_numeric(raw["SEASON_ID"], errors="raise")
    except (ValueError, TypeError) as exc:
        raise PBPValidationError(f"invalid worklist SEASON_ID: {exc}") from exc
    season_id_digit = raw["SEASON_ID"].astype(str).str.strip().str[0]
    expected_digit = _SEASON_ID_DIGITS[game_type]
    if not season_id_digit.eq(expected_digit).all():
        raise PBPValidationError(
            "worklist SEASON_ID leading digit does not match requested "
            f"game type {game_type!r}"
        )
    ending_season = (season_id_numeric.astype("int64") % 10_000) + 1
    if not ending_season.eq(season).all():
        found = sorted(set(int(value) for value in ending_season.unique()))
        raise PBPValidationError(
            f"worklist ending season mismatch: requested {season}, "
            f"found {found}"
        )

    game_id = raw["GAME_ID"].astype(str).str.zfill(10)
    expected_prefix = ID_PREFIXES[game_type]
    prefix_ok = game_id.str.startswith(expected_prefix)
    if not prefix_ok.all():
        bad = sorted(game_id.loc[~prefix_ok].unique().tolist())[:5]
        raise PBPValidationError(
            f"worklist GAME_ID prefix mismatch for game type {game_type!r}: "
            f"{bad}"
        )

    matchup = raw["MATCHUP"].astype(str)
    is_home = matchup.str.contains(r"\svs\.\s", regex=True)
    is_away = matchup.str.contains(r"\s@\s", regex=True)
    if not (is_home ^ is_away).all():
        raise PBPValidationError(
            "worklist MATCHUP must indicate exactly one of home ('vs.') "
            "or away ('@')"
        )

    pts_raw = raw["PTS"]
    if pts_raw.map(lambda value: isinstance(value, bool)).any():
        raise PBPValidationError("worklist PTS must not be boolean")
    try:
        pts_numeric = pd.to_numeric(pts_raw, errors="raise")
    except (ValueError, TypeError) as exc:
        raise PBPValidationError(f"invalid worklist PTS: {exc}") from exc
    if (pts_numeric < 0).any() or not pts_numeric.mod(1).eq(0).all():
        raise PBPValidationError(
            "worklist PTS must be non-negative integers"
        )
    points = pts_numeric.astype("int64")

    franchise_id = raw["TEAM_NAME"].map(
        lambda name: franchises.resolve(str(name), season)
    )
    game_date = pd.to_datetime(raw["GAME_DATE"]).dt.date

    working = pd.DataFrame({
        "game_id": game_id,
        "game_date": game_date,
        "is_home": is_home,
        "franchise_id": franchise_id,
        "pts": points,
    })

    rows = []
    for game_id_value, group in working.groupby("game_id", sort=False):
        if len(group) != 2:
            raise PBPValidationError(
                f"worklist game {game_id_value} has {len(group)} team "
                "rows, expected 2"
            )
        home_rows = group.loc[group["is_home"]]
        if len(home_rows) != 1:
            raise PBPValidationError(
                f"worklist game {game_id_value} has {len(home_rows)} "
                "home rows, expected 1"
            )
        home = home_rows.iloc[0]
        away = group.loc[~group["is_home"]].iloc[0]
        if home["franchise_id"] == away["franchise_id"]:
            raise PBPValidationError(
                f"worklist game {game_id_value} has the same franchise "
                f"{home['franchise_id']!r} on both sides"
            )
        rows.append({
            "game_id": game_id_value,
            "season": season,
            "game_type": game_type,
            "game_date": home["game_date"],
            "away_franchise_id": away["franchise_id"],
            "home_franchise_id": home["franchise_id"],
            "away_pts": int(away["pts"]),
            "home_pts": int(home["pts"]),
        })

    frame = pd.DataFrame(rows, columns=WORKLIST_COLUMNS)
    if frame["game_id"].duplicated().any():
        raise PBPValidationError(
            f"duplicate worklist game ids: {_duplicate_values(frame['game_id'])}"
        )
    frame = frame.sort_values(
        ["season", "game_date", "game_type", "game_id"], kind="stable"
    ).reset_index(drop=True)
    return frame


def load_worklist(
    data_dir: Path, seasons: Iterable[int], *, require_complete: bool
) -> pd.DataFrame:
    """Load and normalize the on-disk worklist files for ``seasons``.

    Regular Season and Playoffs are expected for every season; PlayIn is
    expected only for seasons in ``PLAY_IN_SEASONS``. With
    ``require_complete=True``, any missing expected file raises. With
    ``require_complete=False``, missing files are skipped (used by
    partial/in-progress fetch states in later tasks).
    """
    ordered_seasons = sorted({int(season) for season in seasons})
    frames: list[pd.DataFrame] = []
    missing: list[str] = []
    for season in ordered_seasons:
        expected_types = ["regular", "playoff"]
        if season in PLAY_IN_SEASONS:
            expected_types.append("play_in")
        for game_type in expected_types:
            path = worklist_path(data_dir, season, game_type)
            if not path.is_file():
                if require_complete:
                    missing.append(str(path))
                continue
            frames.append(
                normalize_worklist_payload(
                    path.read_bytes(), season=season, game_type=game_type
                )
            )
    if missing:
        raise PBPValidationError(
            f"missing required worklist file(s): {sorted(missing)}"
        )
    if not frames:
        return pd.DataFrame(columns=WORKLIST_COLUMNS)
    combined = pd.concat(frames, ignore_index=True)
    if combined["game_id"].duplicated().any():
        raise PBPValidationError(
            "duplicate worklist game ids across worklist files: "
            f"{_duplicate_values(combined['game_id'])}"
        )
    combined = combined.sort_values(
        ["season", "game_date", "game_type", "game_id"], kind="stable"
    ).reset_index(drop=True)
    return combined


def _mismatch_sample(
    comparison: pd.DataFrame, side: str, key: Sequence[str]
) -> list[dict]:
    rows = comparison.loc[comparison["_merge"].eq(side), list(key)].copy()
    if rows.empty:
        return []
    rows = rows.sort_values(list(key), kind="stable")
    return rows.head(_MISMATCH_SAMPLE_ROWS).to_dict("records")


def _require_unique_key(frame: pd.DataFrame, key: Sequence[str], label: str) -> None:
    if frame.duplicated(list(key)).any():
        raise PBPValidationError(f"duplicate {label} comparison key")


def cross_validate_worklist(
    worklist: pd.DataFrame, data_dir: Path, seasons: Iterable[int]
) -> None:
    """Require exact bidirectional equality with the published game tables.

    Regular Season worklist rows are compared against ``games.parquet``.
    Playoffs and PlayIn worklist rows are compared against
    ``playoff_games.parquet`` filtered to ``game_type in
    {"playoff", "play_in"}`` (``tiebreaker``/``voided`` rows are excluded
    from the expected set). Both comparisons require every row to match on
    both sides; any unmatched row on either side raises.
    """
    scoped_seasons = sorted({int(season) for season in seasons})

    if worklist["game_id"].duplicated().any():
        raise PBPValidationError(
            "duplicate worklist game ids (cross-type duplication): "
            f"{_duplicate_values(worklist['game_id'])}"
        )

    games = pd.read_parquet(data_dir / "parquet/games.parquet")
    playoff_games = pd.read_parquet(data_dir / "parquet/playoff_games.parquet")

    worklist_regular = worklist.loc[
        worklist["game_type"].eq("regular")
        & worklist["season"].isin(scoped_seasons),
        _REGULAR_KEY,
    ].copy()
    publication_regular = games.loc[
        games["season"].isin(scoped_seasons), _REGULAR_KEY
    ].copy()
    _require_unique_key(worklist_regular, _REGULAR_KEY, "regular worklist")
    _require_unique_key(publication_regular, _REGULAR_KEY, "regular publication")
    regular_comparison = worklist_regular.merge(
        publication_regular,
        on=_REGULAR_KEY,
        how="outer",
        indicator=True,
        validate="one_to_one",
    )
    if not regular_comparison["_merge"].eq("both").all():
        raise PBPValidationError(
            "regular worklist mismatch: worklist_only="
            f"{_mismatch_sample(regular_comparison, 'left_only', _REGULAR_KEY)}, "
            "publication_only="
            f"{_mismatch_sample(regular_comparison, 'right_only', _REGULAR_KEY)}"
        )

    worklist_postseason = worklist.loc[
        worklist["game_type"].isin(["playoff", "play_in"])
        & worklist["season"].isin(scoped_seasons),
        _POSTSEASON_KEY,
    ].copy()
    publication_postseason = playoff_games.loc[
        playoff_games["season"].isin(scoped_seasons)
        & playoff_games["game_type"].isin(["playoff", "play_in"]),
        _POSTSEASON_KEY,
    ].copy()
    _require_unique_key(
        worklist_postseason, _POSTSEASON_KEY, "postseason worklist"
    )
    _require_unique_key(
        publication_postseason, _POSTSEASON_KEY, "postseason publication"
    )
    postseason_comparison = worklist_postseason.merge(
        publication_postseason,
        on=_POSTSEASON_KEY,
        how="outer",
        indicator=True,
        validate="one_to_one",
    )
    if not postseason_comparison["_merge"].eq("both").all():
        raise PBPValidationError(
            "postseason worklist mismatch: worklist_only="
            f"{_mismatch_sample(postseason_comparison, 'left_only', _POSTSEASON_KEY)}, "
            "publication_only="
            f"{_mismatch_sample(postseason_comparison, 'right_only', _POSTSEASON_KEY)}"
        )


# ---------------------------------------------------------------------------
# Transport validators
#
# Transport validation is deliberately shallow: it preserves a nonempty,
# correct-game JSON envelope as cached raw evidence before the fixed V3
# schema parser (``pbp_build.parse_game_payload``) runs. Schema drift in the
# action shape is a build-time concern, not a transport-time one.
# ---------------------------------------------------------------------------


def validate_worklist_transport(
    path: Path,
    *,
    season: int,
    game_type: str,
) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    normalize_worklist_payload(
        json.dumps(payload).encode("utf-8"),
        season=season,
        game_type=game_type,
    )


def validate_game_transport(path: Path, *, game_id: str) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    game = payload.get("game")
    if not isinstance(game, dict) or game.get("gameId") != game_id:
        raise PBPValidationError(f"wrong outer game ID for {path}")
    actions = game.get("actions")
    if (
        not isinstance(actions, list)
        or not actions
        or any(not isinstance(action, dict) for action in actions)
    ):
        raise PBPValidationError(f"invalid action envelope for {path}")


# ---------------------------------------------------------------------------
# Exact cache identity: expected metadata subsets and comparison
#
# ``publish_worklist_payload``/``publish_game_payload`` derive every
# identity field they *can* know from the destination path plus fixed
# constants, and reject a mismatched ``metadata`` argument before writing
# anything. A game pair's ``game_type`` is the one identity field that
# cannot be derived from its path (``games/<season>/<game_id>.json`` has no
# game-type component); only cache-tree validation -- which has the
# cross-referenced worklist row -- can check it.
# ---------------------------------------------------------------------------

_WORKLIST_PROVIDER = "stats_nba"
_WORKLIST_RESOURCE = "pbp_worklist"
_WORKLIST_ENDPOINT = "LeagueGameFinder"
_GAME_PROVIDER = "stats_nba"
_GAME_RESOURCE = "play_by_play"
_GAME_ENDPOINT = "PlayByPlayV3"

_CACHE_MANAGED_METADATA_KEYS = frozenset({"fetched_at", "content_length", "sha256"})


def _expected_worklist_identity(season: int, game_type: str) -> dict:
    return {
        "provider": _WORKLIST_PROVIDER,
        "resource": _WORKLIST_RESOURCE,
        "endpoint": _WORKLIST_ENDPOINT,
        "parameters": {
            "Season": season_label(season),
            "SeasonType": SEASON_TYPES[game_type],
        },
        "season": season,
        "season_label": season_label(season),
        "game_type": game_type,
    }


def _expected_game_identity(season: int, game_type: str, game_id: str) -> dict:
    return {
        "provider": _GAME_PROVIDER,
        "resource": _GAME_RESOURCE,
        "endpoint": _GAME_ENDPOINT,
        "parameters": {
            "GameID": game_id,
            "StartPeriod": 0,
            "EndPeriod": 0,
        },
        "season": season,
        "season_label": season_label(season),
        "game_type": game_type,
        "game_id": game_id,
    }


def _expected_game_identity_without_type(season: int, game_id: str) -> dict:
    # ``game_type`` cannot be derived from the game cache path alone; the
    # placeholder is discarded immediately and never compared.
    identity = _expected_game_identity(season, "__unknown__", game_id)
    del identity["game_type"]
    return identity


def _identity_mismatch(observed: object, expected: object) -> bool:
    if isinstance(expected, dict):
        if not isinstance(observed, dict) or set(observed) != set(expected):
            return True
        return any(
            _identity_mismatch(observed[key], value)
            for key, value in expected.items()
        )
    return type(observed) is not type(expected) or observed != expected


def _require_exact_identity(
    observed: Mapping[str, object], expected: Mapping[str, object], *, label: str
) -> None:
    for key, value in expected.items():
        if key not in observed or _identity_mismatch(observed[key], value):
            raise cache.CacheIntegrityError(
                f"PBP cache metadata mismatch for {label}: key {key!r}"
            )


def _season_from_destination(destination: Path, *, label: str) -> int:
    try:
        return int(destination.parent.name)
    except ValueError as exc:
        raise PBPValidationError(
            f"cannot derive season from {label} destination {destination}"
        ) from exc


def _require_valid_game_type_shape(
    metadata: Mapping[str, object], *, label: str
) -> None:
    # A game pair's true ``game_type`` can only be known by cross-referencing
    # the worklist row that names it (see ``_read_game_pair``), which is not
    # available here. This is the minimum publish-time check possible: the
    # value must at least be a well-formed member of GAME_TYPES.
    game_type = metadata.get("game_type")
    if type(game_type) is not str or game_type not in GAME_TYPES:
        raise cache.CacheIntegrityError(
            f"PBP cache metadata mismatch for {label}: key 'game_type'"
        )


# ---------------------------------------------------------------------------
# Publish wrappers
# ---------------------------------------------------------------------------


def publish_worklist_payload(
    destination: Path, payload: bytes, metadata: Mapping[str, object]
) -> Path:
    destination = Path(destination)
    game_type = destination.stem
    season = _season_from_destination(destination, label="worklist")
    _require_exact_identity(
        metadata,
        _expected_worklist_identity(season, game_type),
        label=str(destination),
    )
    validator = functools.partial(
        validate_worklist_transport, season=season, game_type=game_type
    )
    return cache.publish_bytes(destination, payload, dict(metadata), validator)


def publish_game_payload(
    destination: Path, payload: bytes, metadata: Mapping[str, object]
) -> Path:
    destination = Path(destination)
    game_id = destination.stem
    season = _season_from_destination(destination, label="game")
    _require_exact_identity(
        metadata,
        _expected_game_identity_without_type(season, game_id),
        label=str(destination),
    )
    _require_valid_game_type_shape(metadata, label=str(destination))
    validator = functools.partial(validate_game_transport, game_id=game_id)
    return cache.publish_bytes(destination, payload, dict(metadata), validator)


# ---------------------------------------------------------------------------
# Exact metadata readers -- used by cache-tree validation to re-check every
# on-disk pair (including ones this process never published) after
# checksum/transport validation succeeds.
# ---------------------------------------------------------------------------


def _read_worklist_pair(
    body_path: Path, *, season: int, game_type: str
) -> bytes | None:
    validator = functools.partial(
        validate_worklist_transport, season=season, game_type=game_type
    )
    payload = cache.read_valid(body_path, validator)
    if payload is None:
        return None
    sidecar_path = body_path.with_name(body_path.name + ".meta.json")
    metadata = json.loads(sidecar_path.read_text(encoding="utf-8"))
    _require_exact_identity(
        metadata,
        _expected_worklist_identity(season, game_type),
        label=str(body_path),
    )
    return payload


def _read_game_pair(
    body_path: Path, *, season: int, game_type: str, game_id: str
) -> bytes | None:
    validator = functools.partial(validate_game_transport, game_id=game_id)
    payload = cache.read_valid(body_path, validator)
    if payload is None:
        return None
    sidecar_path = body_path.with_name(body_path.name + ".meta.json")
    metadata = json.loads(sidecar_path.read_text(encoding="utf-8"))
    _require_exact_identity(
        metadata,
        _expected_game_identity(season, game_type, game_id),
        label=str(body_path),
    )
    return payload


# ---------------------------------------------------------------------------
# Safe recursive cache-tree inventory
#
# Every path is derived from a fixed contract before it is opened: the
# season/type worklist contract, each checksum-valid normalized worklist,
# and each worklist row's season/game ID. ``lstat`` (never ``stat``) governs
# every type check so a symlink or FIFO can never be treated as a regular
# file or directory.
# ---------------------------------------------------------------------------


def _lstat_or_none(path: Path):
    try:
        return path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return None


def _require_allowed_entries(
    dir_path: Path, allowed: Mapping[str, str], relative_dir: str
) -> None:
    """Reject any entry whose name or filesystem type has no exact contract."""
    for name in sorted(entry.name for entry in dir_path.iterdir()):
        relative = f"{relative_dir}/{name}" if relative_dir else name
        kind = allowed.get(name)
        if kind is None:
            raise PBPValidationError(f"unexpected cache entry: {relative}")
        info = (dir_path / name).lstat()
        if kind == "dir" and not stat.S_ISDIR(info.st_mode):
            raise PBPValidationError(
                f"expected a directory at {relative}, found an unsafe entry"
            )
        if kind == "file" and not stat.S_ISREG(info.st_mode):
            raise PBPValidationError(
                f"expected a regular file at {relative}, found an unsafe entry"
            )


def validate_cache_tree(
    raw_root: Path, *, required_seasons: Iterable[int], require_complete: bool
) -> CacheInventory:
    raw_root = Path(raw_root)
    ordered_seasons = sorted({int(season) for season in required_seasons})
    required_season_names = {str(season) for season in ordered_seasons}

    root_info = _lstat_or_none(raw_root)
    if root_info is not None:
        if not stat.S_ISDIR(root_info.st_mode):
            raise PBPValidationError(
                f"expected a directory at the PBP cache root, found an "
                f"unsafe entry: {raw_root}"
            )
        _require_allowed_entries(raw_root, {"worklists": "dir", "games": "dir"}, "")

    worklists_root = raw_root / "worklists"
    games_root = raw_root / "games"

    worklists_info = _lstat_or_none(worklists_root)
    if worklists_info is not None:
        if not stat.S_ISDIR(worklists_info.st_mode):
            raise PBPValidationError(
                f"expected a directory at worklists, found an unsafe entry: "
                f"{worklists_root}"
            )
        _require_allowed_entries(
            worklists_root,
            {name: "dir" for name in required_season_names},
            "worklists",
        )

    missing_worklists: list[str] = []
    worklist_body_paths: list[Path] = []
    worklist_frames: list[pd.DataFrame] = []

    for season in ordered_seasons:
        applicable_types = ["regular", "playoff"]
        if season in PLAY_IN_SEASONS:
            applicable_types.append("play_in")
        season_dir = worklists_root / str(season)
        season_info = _lstat_or_none(season_dir)
        if season_info is None:
            if require_complete:
                missing_worklists.extend(
                    f"worklists/{season}/{game_type}.json"
                    for game_type in applicable_types
                )
            continue
        allowed_files = {}
        for game_type in applicable_types:
            allowed_files[f"{game_type}.json"] = "file"
            allowed_files[f"{game_type}.json.meta.json"] = "file"
        _require_allowed_entries(season_dir, allowed_files, f"worklists/{season}")
        for game_type in applicable_types:
            body_path = season_dir / f"{game_type}.json"
            payload = _read_worklist_pair(body_path, season=season, game_type=game_type)
            if payload is None:
                if require_complete:
                    missing_worklists.append(f"worklists/{season}/{game_type}.json")
                continue
            worklist_body_paths.append(body_path)
            worklist_frames.append(
                normalize_worklist_payload(payload, season=season, game_type=game_type)
            )

    if require_complete and missing_worklists:
        raise PBPValidationError(
            f"missing required worklist file(s): {sorted(missing_worklists)}"
        )

    if worklist_frames:
        combined_worklist = pd.concat(worklist_frames, ignore_index=True)
        if combined_worklist["game_id"].duplicated().any():
            raise PBPValidationError(
                "duplicate worklist game ids in cache tree: "
                f"{_duplicate_values(combined_worklist['game_id'])}"
            )
        combined_worklist = combined_worklist.sort_values(
            ["season", "game_date", "game_type", "game_id"], kind="stable"
        ).reset_index(drop=True)
    else:
        combined_worklist = pd.DataFrame(columns=WORKLIST_COLUMNS)

    games_info = _lstat_or_none(games_root)
    if games_info is not None:
        if not stat.S_ISDIR(games_info.st_mode):
            raise PBPValidationError(
                f"expected a directory at games, found an unsafe entry: "
                f"{games_root}"
            )
        _require_allowed_entries(
            games_root, {name: "dir" for name in required_season_names}, "games"
        )

    missing_games: list[str] = []
    game_body_paths: list[Path] = []

    for season in ordered_seasons:
        season_rows = combined_worklist.loc[combined_worklist["season"].eq(season)]
        season_dir = games_root / str(season)
        season_info = _lstat_or_none(season_dir)
        if season_info is None:
            if require_complete:
                missing_games.extend(
                    f"games/{season}/{game_id}.json"
                    for game_id in season_rows["game_id"]
                )
            continue
        allowed_files = {}
        for game_id in season_rows["game_id"]:
            allowed_files[f"{game_id}.json"] = "file"
            allowed_files[f"{game_id}.json.meta.json"] = "file"
        _require_allowed_entries(season_dir, allowed_files, f"games/{season}")
        for _, row in season_rows.iterrows():
            game_id = row["game_id"]
            game_type = row["game_type"]
            body_path = season_dir / f"{game_id}.json"
            payload = _read_game_pair(
                body_path, season=season, game_type=game_type, game_id=game_id
            )
            if payload is None:
                if require_complete:
                    missing_games.append(f"games/{season}/{game_id}.json")
                continue
            game_body_paths.append(body_path)

    if require_complete and missing_games:
        raise PBPValidationError(
            f"missing required game file(s): {sorted(missing_games)}"
        )

    return CacheInventory(
        worklist_bodies=tuple(sorted(worklist_body_paths)),
        game_bodies=tuple(sorted(game_body_paths)),
        worklist=combined_worklist,
    )


def validate_cache_inventory(
    data_dir: Path, *, required_seasons: Iterable[int], require_complete: bool
) -> CacheInventory:
    return validate_cache_tree(
        Path(data_dir) / "raw/stats_nba/pbp",
        required_seasons=required_seasons,
        require_complete=require_complete,
    )
