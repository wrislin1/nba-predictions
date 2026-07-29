"""Fetch and normalize cached Basketball-Reference historical schedules."""

from dataclasses import dataclass
import re
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

from lxml import html
import pandas as pd
import requests

from nba_data import bref, cache


BASE_URL = "https://www.basketball-reference.com"
HISTORICAL_SEASONS = tuple(range(1947, 1984))
CALENDAR_MONTHS = (
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
)
REQUIRED_STATS = frozenset({
    "date_game", "visitor_team_name", "visitor_pts", "home_team_name",
    "home_pts", "box_score_text", "overtimes",
})
OVERTIME_PATTERN = re.compile(r"^(?:(\d+)OT|OT)?$")
BOXSCORE_PATTERN = re.compile(r"^/boxscores/(\d{9}[A-Z0-9]{3})\.html$")
EXPLICIT_NEUTRAL_REMARKS = frozenset({"neutral site"})


@dataclass(frozen=True, order=True)
class HistoricalPage:
    month: str
    url: str
    filename: str


def league_for_season(season: int) -> str:
    if season not in HISTORICAL_SEASONS:
        raise ValueError(f"historical season must be 1947..1983: {season}")
    return "BAA" if season <= 1949 else "NBA"


def index_filename(season: int) -> str:
    return f"{league_for_season(season)}_{season}_games.html"


def index_url(season: int) -> str:
    return f"{BASE_URL}/leagues/{index_filename(season)}"


def _document(payload: bytes, label: str):
    try:
        return html.fromstring(payload)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{label}: invalid HTML") from exc


def discover_month_pages(
    payload: bytes,
    season: int,
) -> tuple[HistoricalPage, ...]:
    league = league_for_season(season)
    root = _document(payload, index_url(season))
    pattern = re.compile(rf"^/leagues/{league}_{season}_games-([a-z]+)\.html$")
    pages = {}
    for href in root.xpath("//a/@href"):
        match = pattern.fullmatch(str(href))
        if match is None:
            continue
        month = match.group(1)
        if month not in CALENDAR_MONTHS:
            continue
        page = HistoricalPage(
            month=month,
            url=urljoin(BASE_URL, str(href)),
            filename=f"{league}_{season}_games-{month}.html",
        )
        pages[page.url] = page
    if not pages:
        raise ValueError(f"{index_url(season)}: no regular-season month links")
    return tuple(pages.values())


def _cell_text(row, stat: str) -> str:
    cells = row.xpath(f"./th[@data-stat='{stat}'] | ./td[@data-stat='{stat}']")
    if not cells:
        return ""
    return " ".join("".join(cells[0].itertext()).split())


def _cell_href(row, stat: str) -> str | None:
    hrefs = row.xpath(
        f"./th[@data-stat='{stat}']//a/@href | "
        f"./td[@data-stat='{stat}']//a/@href"
    )
    return str(hrefs[0]) if hrefs else None


def _overtime_periods(marker: str) -> int:
    normalized = marker.strip().upper()
    match = OVERTIME_PATTERN.fullmatch(normalized)
    if match is None:
        raise ValueError(f"unsupported overtime marker: {marker!r}")
    if not normalized:
        return 0
    return int(match.group(1) or 1)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _attendance(value: str) -> tuple[int | None, str]:
    if not value.strip():
        return None, "missing"
    parsed = int(value.replace(",", ""))
    return parsed, "reported_zero" if parsed == 0 else "reported_positive"


def parse_schedule_page(
    payload: bytes,
    *,
    season: int,
    schedule_url: str,
) -> pd.DataFrame:
    league = league_for_season(season)
    root = _document(payload, schedule_url)
    tables = root.xpath("//table[@id='schedule']")
    if len(tables) != 1:
        raise ValueError(
            f"{schedule_url}: expected one schedule table, found {len(tables)}"
        )
    stats = set(tables[0].xpath(".//*[@data-stat]/@data-stat"))
    missing = sorted(REQUIRED_STATS - stats)
    if missing:
        raise ValueError(f"{schedule_url}: missing schedule stats {missing}")
    records = []
    for source_row, row in enumerate(tables[0].xpath("./tbody/tr"), start=1):
        away_pts = _cell_text(row, "visitor_pts")
        home_pts = _cell_text(row, "home_pts")
        if not away_pts and not home_pts:
            continue
        box_href = _cell_href(row, "box_score_text")
        box_match = BOXSCORE_PATTERN.fullmatch(box_href or "")
        attendance, attendance_status = _attendance(_cell_text(row, "attendance"))
        remarks = _cell_text(row, "game_remarks")
        normalized_remarks = remarks.casefold()
        site_status = (
            "home" if not remarks else (
                "neutral" if normalized_remarks in EXPLICIT_NEUTRAL_REMARKS
                else "unknown"
            )
        )
        records.append({
            "season": season,
            "league": league,
            "game_date": pd.to_datetime(
                _cell_text(row, "date_game"), errors="raise"
            ).date(),
            "away_team_name": _cell_text(row, "visitor_team_name"),
            "home_team_name": _cell_text(row, "home_team_name"),
            "away_pts": int(away_pts),
            "home_pts": int(home_pts),
            "overtime_marker": _cell_text(row, "overtimes"),
            "overtime_periods": _overtime_periods(_cell_text(row, "overtimes")),
            "site_status": site_status,
            "venue_name": _cell_text(row, "arena_name") or pd.NA,
            "attendance": attendance,
            "attendance_status": attendance_status,
            "boxscore_url": urljoin(BASE_URL, box_href) if box_match else pd.NA,
            "boxscore_slug": box_match.group(1) if box_match else pd.NA,
            "schedule_url": schedule_url,
            "game_remarks": remarks,
            "source_row": source_row,
        })
    return _assign_game_ids(pd.DataFrame(records))


def _assign_game_ids(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.sort_values(
        ["game_date", "schedule_url", "source_row"], kind="stable"
    ).reset_index(drop=True)
    ordinals = (
        frame.groupby(
            ["game_date", "away_team_name", "home_team_name"], sort=False
        ).cumcount().add(1)
    )
    fallback_ids = [
        (
            f"bref-fallback-{date:%Y%m%d}-{_slug(away)}"
            f"-at-{_slug(home)}-{int(ordinal):02d}"
        )
        for date, away, home, ordinal in zip(
            frame["game_date"], frame["away_team_name"],
            frame["home_team_name"], ordinals, strict=True,
        )
    ]
    frame["game_id"] = pd.Series(fallback_ids, dtype="string")
    has_boxscore = frame["boxscore_slug"].notna()
    frame.loc[has_boxscore, "game_id"] = (
        "bref-" + frame.loc[has_boxscore, "boxscore_slug"].astype("string")
    )
    for column in (
        "league", "away_team_name", "home_team_name", "overtime_marker",
        "site_status", "venue_name", "attendance_status", "boxscore_url",
        "schedule_url", "game_remarks",
    ):
        frame[column] = frame[column].astype("string")
    frame["attendance"] = frame["attendance"].astype("Int64")
    return frame


def validate_index(path: Path, season: int) -> None:
    discover_month_pages(path.read_bytes(), season)


def validate_month(path: Path, season: int, url: str) -> None:
    parse_schedule_page(path.read_bytes(), season=season, schedule_url=url)


def _request(session: requests.Session, url: str) -> bytes:
    try:
        response = session.get(url, timeout=30, verify=True)
    except requests.RequestException as exc:
        raise cache.SourceAccessError(
            f"{url} failed: {type(exc).__name__}: {exc}"
        ) from exc
    if response.status_code != 200:
        raise cache.SourceAccessError(f"{url} returned HTTP {response.status_code}")
    return response.content


def fetch(
    raw_dir: Path,
    seasons: Iterable[int] = HISTORICAL_SEASONS,
    sleep_s: float = 12,
) -> None:
    cache_dir = raw_dir / "historical_bref"
    session = requests.Session()
    session.headers.update({"User-Agent": bref.USER_AGENT})
    requested = False

    def obtain(path: Path, url: str, metadata: dict, validator) -> bytes:
        nonlocal requested
        cached = cache.read_valid(path, validator)
        if cached is not None:
            return cached
        if requested:
            time.sleep(sleep_s)
        payload = _request(session, url)
        try:
            cache.publish_bytes(path, payload, metadata, validator)
        except Exception:
            path.with_name(path.name + ".part").unlink(missing_ok=True)
            path.with_name(path.name + ".meta.json.part").unlink(
                missing_ok=True
            )
            raise
        requested = True
        return payload

    for season in tuple(seasons):
        league = league_for_season(season)
        index_path = cache_dir / index_filename(season)
        page_payload = obtain(
            index_path,
            index_url(season),
            {
                "provider": "basketball_reference",
                "resource": "historical_schedule_index",
                "league": league,
                "season": season,
                "url": index_url(season),
            },
            lambda path, value=season: validate_index(path, value),
        )
        for page in discover_month_pages(page_payload, season):
            destination = cache_dir / page.filename
            obtain(
                destination,
                page.url,
                {
                    "provider": "basketball_reference",
                    "resource": "historical_schedule_month",
                    "league": league,
                    "season": season,
                    "month": page.month,
                    "url": page.url,
                },
                lambda path, value=season, url=page.url: validate_month(
                    path, value, url
                ),
            )


def load_season(raw_dir: Path, season: int) -> pd.DataFrame:
    cache_dir = raw_dir / "historical_bref"
    index_path = cache_dir / index_filename(season)
    payload = cache.require_valid(
        index_path,
        lambda path: validate_index(path, season),
    )
    frames = []
    for page in discover_month_pages(payload, season):
        month_payload = cache.require_valid(
            cache_dir / page.filename,
            lambda path, value=season, url=page.url: validate_month(
                path, value, url
            ),
        )
        frames.append(parse_schedule_page(
            month_payload, season=season, schedule_url=page.url
        ))
    combined = pd.concat(frames, ignore_index=True)
    combined = _assign_game_ids(combined.drop(columns=["game_id"]))
    return combined.sort_values(
        ["game_date", "game_id"], kind="stable"
    ).reset_index(drop=True)
