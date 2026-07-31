"""Load cached Basketball-Reference schedule attendance rows."""

import datetime
from io import BytesIO
from pathlib import Path
import time
from typing import Iterable, NamedTuple

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


class BrefPolicy(NamedTuple):
    """Recursively immutable Basketball-Reference cache policy."""

    standard: tuple[str, ...]
    months_by_season: tuple[tuple[int, tuple[str, ...]], ...]
    user_agent: str
    schedule_columns: tuple[str, ...]
    output_columns: tuple[str, ...]

    def months_for_season(self, season: int) -> tuple[str, ...]:
        for configured_season, months in self.months_by_season:
            if configured_season == season:
                return months
        raise KeyError(season)


def capture_policy() -> BrefPolicy:
    """Snapshot the current module policy without mutable aliases."""
    return BrefPolicy(
        standard=tuple(str(month) for month in STANDARD),
        months_by_season=tuple(sorted(
            (
                int(season),
                tuple(str(month) for month in months),
            )
            for season, months in MONTHS_BY_SEASON.items()
        )),
        user_agent=str(USER_AGENT),
        schedule_columns=tuple(str(column) for column in SCHEDULE_COLUMNS),
        output_columns=tuple(str(column) for column in OUTPUT_COLUMNS),
    )


def _policy_or_current(policy: BrefPolicy | None) -> BrefPolicy:
    return capture_policy() if policy is None else policy


def _read_schedule(
    source: Path | bytes,
    *,
    policy: BrefPolicy | None = None,
) -> pd.DataFrame:
    policy = _policy_or_current(policy)
    reader = BytesIO(source) if isinstance(source, bytes) else source
    label = "response" if isinstance(source, bytes) else str(source)
    tables = pd.read_html(reader, attrs={"id": "schedule"})
    if len(tables) != 1:
        raise ValueError(f"{label}: expected exactly one schedule table, found {len(tables)}")
    table = tables[0]
    if table.shape[1] != len(policy.schedule_columns):
        raise ValueError(
            f"{label}: expected {len(policy.schedule_columns)} schedule columns, "
            f"found {table.shape[1]}"
        )
    table.columns = policy.schedule_columns
    return table


def validate_payload(
    path: Path,
    *,
    policy: BrefPolicy | None = None,
) -> None:
    """Confirm a cache entry contains the expected schedule table schema."""
    policy = _policy_or_current(policy)
    validate_payload_bytes(path.read_bytes(), policy=policy)


def validate_payload_bytes(
    payload: bytes,
    *,
    policy: BrefPolicy | None = None,
) -> None:
    """Confirm captured bytes contain the expected schedule table schema."""
    _read_schedule(payload, policy=policy)


def fetch(
    raw_dir: Path,
    seasons: Iterable[int],
    sleep_s: float = 12,
    *,
    policy: BrefPolicy | None = None,
) -> None:
    """Cache requested Basketball-Reference schedule pages with polite cadence."""
    policy = _policy_or_current(policy)
    validator = lambda path: validate_payload(path, policy=policy)
    session = None
    requested = False
    for season in seasons:
        for month in policy.months_for_season(season):
            destination = raw_dir / "bref" / f"NBA_{season}_games-{month}.html"
            if cache.read_valid(destination, validator) is not None:
                continue
            if requested:
                time.sleep(sleep_s)
            if session is None:
                session = requests.Session()
                session.headers.update({"User-Agent": policy.user_agent})
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
            _read_schedule(response.content, policy=policy)
            cache.publish_bytes(
                destination,
                response.content,
                {"provider": "basketball_reference", "season": season, "month": month},
                validator,
            )
            requested = True


def load_attendance(
    raw_dir: Path,
    season: int,
    months: Iterable[str] | None = None,
    *,
    policy: BrefPolicy | None = None,
) -> pd.DataFrame:
    """Normalize completed cached Basketball-Reference schedule rows."""
    policy = _policy_or_current(policy)
    validator = lambda path: validate_payload(path, policy=policy)
    selected_months = (
        policy.months_for_season(season)
        if months is None else tuple(months)
    )
    frames = []
    for month in selected_months:
        path = raw_dir / "bref" / f"NBA_{season}_games-{month}.html"
        payload = cache.require_valid(path, validator)
        raw = _read_schedule(payload, policy=policy)
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
        return pd.DataFrame(columns=policy.output_columns)
    return pd.concat(frames, ignore_index=True).sort_values(
        ["game_date", "home_franchise_id", "away_franchise_id"], kind="stable"
    ).reset_index(drop=True)
