"""Fetch and normalize pinned hoopR NBA schedule enrichment rows."""

import json
from io import BytesIO
from importlib import resources
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd
import pyarrow.parquet as pq
import requests

from nba_data import cache, franchises
from nba_data.stats_nba import EXPECTED_GAMES


SNAPSHOT_PATH = resources.files("nba_data.reference").joinpath(
    "hoopr_snapshot.json"
)
SNAPSHOT = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

POSTSEASON_COLUMNS = (
    "season",
    "game_date",
    "game_type",
    "away_franchise_id",
    "home_franchise_id",
    "away_pts",
    "home_pts",
)

_CROSS_SOURCE_KEY = POSTSEASON_COLUMNS


def validate_payload(path: Path) -> None:
    """Confirm that a cached schedule response is readable Parquet."""
    validate_payload_bytes(path.read_bytes())


def validate_payload_bytes(payload: bytes) -> None:
    """Confirm that captured schedule bytes are readable Parquet."""
    pq.ParquetFile(BytesIO(payload))


def _read_pinned_cache(path: Path, season: int) -> bytes | None:
    """Accept a cache only when its sidecar names the exact source pin."""
    payload = cache.read_valid(path, validate_payload)
    if payload is None:
        return None
    sidecar = path.with_name(path.name + ".meta.json")
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    expected = {
        "provider": "hoopr",
        "season": season,
        "upstream_commit": SNAPSHOT["commit"],
    }
    observed = {key: metadata.get(key) for key in expected}
    exact_types = all(
        type(observed[key]) is type(expected[key]) for key in expected
    )
    if observed != expected or not exact_types:
        raise cache.CacheIntegrityError(
            f"hoopR cache metadata mismatch for {path}: "
            f"expected {expected}, observed {observed}"
        )
    return payload


def fetch(raw_dir: Path, seasons: Iterable[int] = SNAPSHOT["seasons"]) -> None:
    """Cache one verified schedule parquet for each requested ending season."""
    session = requests.Session()
    for season in seasons:
        destination = raw_dir / "hoopr" / f"nba_schedule_{season}.parquet"
        if _read_pinned_cache(destination, season) is not None:
            continue
        url = (
            "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/"
            f"{SNAPSHOT['commit']}/nba/schedules/parquet/nba_schedule_{season}.parquet"
        )
        try:
            response = session.get(url, timeout=120, verify=True)
        except requests.RequestException as exc:
            raise cache.SourceAccessError(
                f"{url} failed: {type(exc).__name__}: {exc}"
            ) from exc
        if response.status_code != 200:
            raise cache.SourceAccessError(f"{url} returned HTTP {response.status_code}")
        cache.publish_bytes(
            destination,
            response.content,
            {
                "provider": "hoopr",
                "upstream_commit": SNAPSHOT["commit"],
                "season": season,
            },
            validate_payload,
        )


def _normalize_game_ids(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    invalid = numeric.isna() | numeric.le(0) | numeric.mod(1).ne(0)
    if invalid.any():
        examples = values.loc[invalid].head().tolist()
        raise ValueError(f"invalid hoopR game_id values: {examples}")
    return numeric.astype("int64").astype("string")


def _normalize_postseason_scores(values: pd.Series, *, label: str) -> pd.Series:
    """Normalize completed postseason scores without accepting invalid finals."""
    numeric = pd.to_numeric(values, errors="coerce")
    invalid = (
        values.map(pd.api.types.is_bool)
        | numeric.isna()
        | numeric.lt(0)
        | numeric.mod(1).ne(0)
    )
    if bool(invalid.any()):
        examples = values.loc[invalid].head().tolist()
        raise ValueError(f"invalid hoopR postseason {label} scores: {examples}")
    return numeric.astype("int64")


def load_postseason(raw_dir: Path, season: int) -> pd.DataFrame:
    """Load pinned hoopR completed playoff and play-in evidence only."""
    if season not in range(2020, 2027):
        raise ValueError(
            f"hoopR postseason season must be 2020..2026: {season}"
        )
    path = raw_dir / "hoopr" / f"nba_schedule_{season}.parquet"
    payload = _read_pinned_cache(path, season)
    if payload is None:
        raise cache.CacheIntegrityError(f"missing cache entry for {path}")
    return load_postseason_bytes(
        payload, season, franchises.resolve
    )


def load_postseason_bytes(
    payload: bytes,
    season: int,
    resolve_franchise: Callable[[str, int], str],
) -> pd.DataFrame:
    """Load postseason evidence from captured bytes and a bound resolver."""
    if season not in range(2020, 2027):
        raise ValueError(
            f"hoopR postseason season must be 2020..2026: {season}"
        )
    raw = pd.read_parquet(BytesIO(payload))
    postseason = raw["season_type"].isin([3, 5])
    status = raw["status_type_completed"]
    invalid_status = postseason & ~status.map(pd.api.types.is_bool)
    if bool(invalid_status.any()):
        examples = status.loc[invalid_status].head().tolist()
        raise ValueError(
            "invalid hoopR postseason status_type_completed values: "
            f"{examples}"
        )
    completed = raw.loc[
        postseason & status.map(lambda value: pd.api.types.is_bool(value) and value)
    ].copy()
    frame = pd.DataFrame({
        "season": season,
        "game_date": pd.to_datetime(
            completed["game_date"], errors="raise"
        ).dt.date,
        "game_type": completed["season_type"].map(
            {3: "playoff", 5: "play_in"}
        ).astype("string"),
        "away_franchise_id": completed["away_display_name"].map(
            lambda name: resolve_franchise(str(name), season)
        ),
        "home_franchise_id": completed["home_display_name"].map(
            lambda name: resolve_franchise(str(name), season)
        ),
        "away_pts": _normalize_postseason_scores(
            completed["away_score"], label="away"
        ),
        "home_pts": _normalize_postseason_scores(
            completed["home_score"], label="home"
        ),
    }, columns=POSTSEASON_COLUMNS)
    if bool(frame.isna().any().any()):
        raise ValueError("hoopR postseason published fields must be non-null")
    if bool(frame["away_franchise_id"].eq(frame["home_franchise_id"]).any()):
        raise ValueError("hoopR postseason rows must name different franchises")
    if bool(frame["away_pts"].eq(frame["home_pts"]).any()):
        raise ValueError("hoopR postseason final scores must not be tied")
    if bool(frame.duplicated(list(_CROSS_SOURCE_KEY)).any()):
        raise ValueError("hoopR postseason contains duplicate CROSS_SOURCE_KEY values")
    return frame.sort_values(
        ["game_date", "game_type", "away_franchise_id", "home_franchise_id"],
        kind="stable",
    ).reset_index(drop=True)


def load_season(
    raw_dir: Path, season: int, expected_games: int | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load completed regular-season rows and explicit schedule exclusions."""
    path = raw_dir / "hoopr" / f"nba_schedule_{season}.parquet"
    payload = _read_pinned_cache(path, season)
    if payload is None:
        raise cache.CacheIntegrityError(f"missing cache entry for {path}")
    raw = pd.read_parquet(BytesIO(payload))
    completed = raw.loc[
        (raw["season_type"] == 2) & raw["status_type_completed"].astype(bool)
    ].copy()
    game_type = completed["type_abbreviation"].astype("string").str.strip().str.upper()
    notes = completed["notes_headline"].astype("string").str.strip().str.upper()
    exhibition = game_type.eq("ALLSTAR") | notes.eq("RISING STARS")
    cup_championship = game_type.eq("CC")
    excluded = pd.concat([
        completed.loc[exhibition].assign(reason="exhibition"),
        completed.loc[cup_championship].assign(reason="cup_championship"),
    ], ignore_index=True)
    retained = completed.loc[~(exhibition | cup_championship)].copy()

    home_ids = retained["home_display_name"].map(
        lambda name: franchises.resolve(str(name), season)
    )
    away_ids = retained["away_display_name"].map(
        lambda name: franchises.resolve(str(name), season)
    )
    expected = EXPECTED_GAMES[season] if expected_games is None else expected_games
    if len(retained) != expected:
        raise ValueError(f"season {season}: {len(retained)} games, expected {expected}")

    espn_game_ids = _normalize_game_ids(retained["game_id"])
    schedule = pd.DataFrame({
        "espn_game_id": "espn-" + espn_game_ids,
        "season": season,
        "game_date": retained["game_date"],
        "home_franchise_id": home_ids,
        "away_franchise_id": away_ids,
        "neutral_site": retained["neutral_site"].astype(bool),
        "venue_id": retained["venue_id"].map(
            lambda value: pd.NA if pd.isna(value) else f"espn-{int(value)}"
        ),
        "venue_name_espn": retained["venue_full_name"].astype("string"),
        "venue_city": retained["venue_address_city"].astype("string"),
        "espn_attendance": pd.to_numeric(
            retained["attendance"], errors="coerce"
        ).astype("Int64"),
    })
    return schedule, excluded
