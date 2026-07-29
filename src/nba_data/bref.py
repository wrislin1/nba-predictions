"""Load cached Basketball-Reference schedule attendance rows."""

import datetime
from io import BytesIO
from pathlib import Path
import time
from typing import Iterable

import pandas as pd
import requests

from nba_data import cache, franchises


STANDARD = (
    "october", "november", "december", "january", "february", "march", "april",
)
MONTHS_BY_SEASON = {
    2020: (
        "october", "november", "december", "january", "february", "march",
        "july", "august",
    ),
    2021: ("december", "january", "february", "march", "april", "may"),
    **{season: STANDARD for season in range(2022, 2027)},
}
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
SCHEDULE_COLUMNS = (
    "date", "start", "away", "away_pts", "home", "home_pts", "box", "ot",
    "attendance", "duration", "arena", "notes",
)
OUTPUT_COLUMNS = (
    "season", "game_date", "home_franchise_id", "away_franchise_id",
    "attendance", "arena",
)


def _read_schedule(source: Path | bytes) -> pd.DataFrame:
    reader = BytesIO(source) if isinstance(source, bytes) else source
    label = "response" if isinstance(source, bytes) else str(source)
    tables = pd.read_html(reader, attrs={"id": "schedule"})
    if len(tables) != 1:
        raise ValueError(f"{label}: expected exactly one schedule table, found {len(tables)}")
    table = tables[0]
    if table.shape[1] != len(SCHEDULE_COLUMNS):
        raise ValueError(
            f"{label}: expected {len(SCHEDULE_COLUMNS)} schedule columns, "
            f"found {table.shape[1]}"
        )
    table.columns = SCHEDULE_COLUMNS
    return table


def validate_payload(path: Path) -> None:
    """Confirm a cache entry contains the expected schedule table schema."""
    _read_schedule(path)


def fetch(raw_dir: Path, seasons: Iterable[int], sleep_s: float = 12) -> None:
    """Cache requested Basketball-Reference schedule pages with polite cadence."""
    session = None
    requested = False
    for season in seasons:
        for month in MONTHS_BY_SEASON[season]:
            destination = raw_dir / "bref" / f"NBA_{season}_games-{month}.html"
            if cache.read_valid(destination, validate_payload) is not None:
                continue
            if requested:
                time.sleep(sleep_s)
            if session is None:
                session = requests.Session()
                session.headers.update({"User-Agent": USER_AGENT})
            url = (
                "https://www.basketball-reference.com/leagues/"
                f"NBA_{season}_games-{month}.html"
            )
            try:
                response = session.get(url, timeout=30, verify=True)
            except requests.RequestException as exc:
                raise cache.SourceAccessError(
                    f"{url} failed: {type(exc).__name__}: {exc}"
                ) from exc
            if response.status_code != 200:
                raise cache.SourceAccessError(f"{url} returned HTTP {response.status_code}")
            _read_schedule(response.content)
            cache.publish_bytes(
                destination,
                response.content,
                {"provider": "basketball_reference", "season": season, "month": month},
                validate_payload,
            )
            requested = True


def load_attendance(
    raw_dir: Path, season: int, months: Iterable[str] | None = None
) -> pd.DataFrame:
    """Normalize completed cached Basketball-Reference schedule rows."""
    selected_months = MONTHS_BY_SEASON[season] if months is None else tuple(months)
    frames = []
    for month in selected_months:
        path = raw_dir / "bref" / f"NBA_{season}_games-{month}.html"
        cache.read_valid(path, validate_payload)
        raw = _read_schedule(path)
        away_pts = pd.to_numeric(raw["away_pts"], errors="coerce")
        home_pts = pd.to_numeric(raw["home_pts"], errors="coerce")
        retained = raw.loc[away_pts.notna() & home_pts.notna()].copy()
        attendance = pd.to_numeric(
            retained["attendance"].astype("string").str.replace(",", "", regex=False),
            errors="coerce",
        ).astype("Int64")
        frames.append(pd.DataFrame({
            "season": season,
            "game_date": pd.to_datetime(retained["date"], errors="raise").map(
                lambda value: datetime.date(value.year, value.month, value.day)
            ),
            "home_franchise_id": retained["home"].map(
                lambda name: franchises.resolve(str(name), season)
            ),
            "away_franchise_id": retained["away"].map(
                lambda name: franchises.resolve(str(name), season)
            ),
            "attendance": attendance,
            "arena": retained["arena"].astype("string"),
        }))
    if not frames:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    return pd.concat(frames, ignore_index=True).sort_values(
        ["game_date", "home_franchise_id", "away_franchise_id"], kind="stable"
    ).reset_index(drop=True)
