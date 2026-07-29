"""Fetch and normalize pinned hoopR NBA schedule enrichment rows."""

import json
from pathlib import Path
from typing import Iterable

import pandas as pd
import pyarrow.parquet as pq
import requests

from nba_data import cache, franchises
from nba_data.stats_nba import EXPECTED_GAMES


SNAPSHOT_PATH = Path(__file__).resolve().parents[2] / "data/reference/hoopr_snapshot.json"
SNAPSHOT = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def validate_payload(path: Path) -> None:
    """Confirm that a cached schedule response is readable Parquet."""
    pq.ParquetFile(path)


def fetch(raw_dir: Path, seasons: Iterable[int] = SNAPSHOT["seasons"]) -> None:
    """Cache one verified schedule parquet for each requested ending season."""
    session = requests.Session()
    for season in seasons:
        destination = raw_dir / "hoopr" / f"nba_schedule_{season}.parquet"
        if cache.read_valid(destination, validate_payload) is not None:
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


def load_season(
    raw_dir: Path, season: int, expected_games: int | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load completed regular-season rows and explicit schedule exclusions."""
    path = raw_dir / "hoopr" / f"nba_schedule_{season}.parquet"
    cache.read_valid(path, validate_payload)
    raw = pd.read_parquet(path)
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

    schedule = pd.DataFrame({
        "espn_game_id": "espn-" + retained["game_id"].astype(str),
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
