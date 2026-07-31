"""Exact play-by-play worklist construction and cross-source validation.

Offline, network-free normalization of the ``LeagueGameFinderResults``
worklist payload plus bidirectional equality against the published
``games.parquet`` / ``playoff_games.parquet`` tables. Fetch, cache-tree,
and publication interfaces are added by later tasks.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import json
from pathlib import Path

import pandas as pd

from nba_data import franchises


class PBPValidationError(ValueError):
    """Raised when a play-by-play worklist or cache violates its contract."""


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
