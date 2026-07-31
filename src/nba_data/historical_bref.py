"""Fetch and normalize cached Basketball-Reference historical schedules."""

import re
import stat
import time
from pathlib import Path
from typing import Iterable, Mapping, NamedTuple
from urllib.parse import urljoin

import pandas as pd
import requests

from nba_data import bref, bref_schedule, cache


BASE_URL = "https://www.basketball-reference.com"
HISTORICAL_SEASONS = tuple(range(1947, 1984))
CALENDAR_MONTHS = (
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
)
EXCLUDED_REGULAR_SEASON_REMARKS = frozenset({
    "tiebreaker",
    "protest; does not count toward record",
})


class HistoricalPage(NamedTuple):
    month: str
    url: str
    filename: str


class _CuratedPlayoffBoundary(NamedTuple):
    first_playoff_game_id: str
    schedule_url: str
    note: str


CURATED_PLAYOFF_BOUNDARIES = {
    1947: _CuratedPlayoffBoundary(
        first_playoff_game_id="bref-194704020PHW",
        schedule_url=f"{BASE_URL}/leagues/BAA_1947_games-april.html",
        note=(
            "The checksum-verified cached April schedule starts with this "
            "April 2 playoff game and has no in-table Playoffs separator."
        ),
    ),
    1980: _CuratedPlayoffBoundary(
        first_playoff_game_id="bref-198004020PHI",
        schedule_url=f"{BASE_URL}/leagues/NBA_1980_games-april.html",
        note=(
            "The checksum-verified cached April schedule starts with this "
            "April 2 playoff game and has no in-table Playoffs separator."
        ),
    ),
}

VERIFIED_DUPLICATE_PLAYOFF_SEPARATORS = {
    f"{BASE_URL}/leagues/BAA_1948_games-march.html": (29, 33),
    f"{BASE_URL}/leagues/NBA_1950_games-march.html": (81, 85),
    f"{BASE_URL}/leagues/NBA_1953_games-march.html": (47, 51),
    f"{BASE_URL}/leagues/NBA_1957_games-march.html": (32, 35),
}

VERIFIED_DUPLICATE_SEPARATOR_REGIONS = {
    f"{BASE_URL}/leagues/NBA_1953_games-march.html": (
        ("bref-195303170SYR", "bref-195303180MLH"),
        ("bref-195303170NYK",),
    ),
}


class HistoricalBrefPolicy(NamedTuple):
    """Recursively immutable historical schedule parsing policy."""

    base_url: str
    historical_seasons: tuple[int, ...]
    calendar_months: tuple[str, ...]
    excluded_regular_season_remarks: frozenset[str]
    curated_playoff_boundaries: tuple[
        tuple[int, _CuratedPlayoffBoundary], ...
    ]
    verified_duplicate_playoff_separators: tuple[
        tuple[str, tuple[int, ...]], ...
    ]
    verified_duplicate_separator_regions: tuple[
        tuple[str, tuple[tuple[str, ...], tuple[str, ...]]], ...
    ]


def capture_policy() -> HistoricalBrefPolicy:
    """Snapshot the current module policy without retaining mutable aliases."""
    return HistoricalBrefPolicy(
        base_url=str(BASE_URL),
        historical_seasons=tuple(HISTORICAL_SEASONS),
        calendar_months=tuple(CALENDAR_MONTHS),
        excluded_regular_season_remarks=frozenset(
            EXCLUDED_REGULAR_SEASON_REMARKS
        ),
        curated_playoff_boundaries=tuple(sorted(
            (
                int(season),
                _CuratedPlayoffBoundary(
                    first_playoff_game_id=str(boundary.first_playoff_game_id),
                    schedule_url=str(boundary.schedule_url),
                    note=str(boundary.note),
                ),
            )
            for season, boundary in CURATED_PLAYOFF_BOUNDARIES.items()
        )),
        verified_duplicate_playoff_separators=tuple(sorted(
            (str(url), tuple(int(row) for row in rows))
            for url, rows in VERIFIED_DUPLICATE_PLAYOFF_SEPARATORS.items()
        )),
        verified_duplicate_separator_regions=tuple(sorted(
            (
                str(url),
                (
                    tuple(str(game_id) for game_id in regions[0]),
                    tuple(str(game_id) for game_id in regions[1]),
                ),
            )
            for url, regions in VERIFIED_DUPLICATE_SEPARATOR_REGIONS.items()
        )),
    )


def _policy_or_current(
    policy: HistoricalBrefPolicy | None,
) -> HistoricalBrefPolicy:
    return capture_policy() if policy is None else policy


def league_for_season(
    season: int,
    *,
    policy: HistoricalBrefPolicy | None = None,
) -> str:
    policy = _policy_or_current(policy)
    if season not in policy.historical_seasons:
        raise ValueError(f"historical season must be 1947..1983: {season}")
    return "BAA" if season <= 1949 else "NBA"


def index_filename(
    season: int,
    *,
    policy: HistoricalBrefPolicy | None = None,
) -> str:
    policy = _policy_or_current(policy)
    return f"{league_for_season(season, policy=policy)}_{season}_games.html"


def index_url(
    season: int,
    *,
    policy: HistoricalBrefPolicy | None = None,
) -> str:
    policy = _policy_or_current(policy)
    return (
        f"{policy.base_url}/leagues/"
        f"{index_filename(season, policy=policy)}"
    )


def discover_month_pages(
    payload: bytes,
    season: int,
    *,
    policy: HistoricalBrefPolicy | None = None,
) -> tuple[HistoricalPage, ...]:
    policy = _policy_or_current(policy)
    league = league_for_season(season, policy=policy)
    source_url = index_url(season, policy=policy)
    root = bref_schedule._document(payload, source_url)
    pattern = re.compile(rf"^/leagues/{league}_{season}_games-([a-z]+)\.html$")
    pages = {}
    for href in root.xpath("//a/@href"):
        match = pattern.fullmatch(str(href))
        if match is None:
            continue
        month = match.group(1)
        if month not in policy.calendar_months:
            continue
        page = HistoricalPage(
            month=month,
            url=urljoin(policy.base_url, str(href)),
            filename=f"{league}_{season}_games-{month}.html",
        )
        pages[page.url] = page
    if not pages:
        raise ValueError(f"{source_url}: no regular-season month links")
    return tuple(pages.values())


def _overtime_periods(marker: str, *, schedule_policy=None) -> int:
    return bref_schedule.overtime_periods(
        marker, policy=schedule_policy
    )


def parse_schedule_page(
    payload: bytes,
    *,
    season: int,
    schedule_url: str,
    policy: HistoricalBrefPolicy | None = None,
    schedule_policy=None,
) -> pd.DataFrame:
    policy = _policy_or_current(policy)
    return bref_schedule.parse_schedule_page(
        payload,
        season=season,
        league=league_for_season(season, policy=policy),
        schedule_url=schedule_url,
        policy=schedule_policy,
    )


def _assign_game_ids(frame: pd.DataFrame) -> pd.DataFrame:
    return bref_schedule.assign_game_ids(frame)


def validate_index(path: Path, season: int) -> None:
    policy = capture_policy()
    validate_index_bytes(path.read_bytes(), season, policy=policy)


def validate_index_bytes(
    payload: bytes,
    season: int,
    *,
    policy: HistoricalBrefPolicy | None = None,
) -> None:
    discover_month_pages(payload, season, policy=policy)


def validate_month(path: Path, season: int, url: str) -> None:
    policy = capture_policy()
    schedule_policy = bref_schedule.capture_policy()
    validate_month_bytes(
        path.read_bytes(),
        season,
        url,
        policy=policy,
        schedule_policy=schedule_policy,
    )


def validate_month_bytes(
    payload: bytes,
    season: int,
    url: str,
    *,
    policy: HistoricalBrefPolicy | None = None,
    schedule_policy=None,
) -> None:
    parse_schedule_page(
        payload,
        season=season,
        schedule_url=url,
        policy=policy,
        schedule_policy=schedule_policy,
    )


def _playoff_separator_source_rows(
    payload: bytes,
    schedule_url: str,
    *,
    policy: HistoricalBrefPolicy | None = None,
) -> tuple[int, ...]:
    policy = _policy_or_current(policy)
    root = bref_schedule._document(payload, schedule_url)
    tables = root.xpath("//table[@id='schedule']")
    if len(tables) != 1:
        raise ValueError(
            f"{schedule_url}: expected one schedule table, found {len(tables)}"
        )
    matches = [
        source_row
        for source_row, row in enumerate(
            tables[0].xpath("./tbody/tr"), start=1
        )
        if "thead" in row.get("class", "").split()
        and " ".join("".join(row.itertext()).split()) == "Playoffs"
    ]
    separator_rows = tuple(matches)
    expected_rows = dict(
        policy.verified_duplicate_playoff_separators
    ).get(schedule_url)
    if expected_rows is not None and separator_rows != expected_rows:
        raise ValueError(
            f"{schedule_url}: verified duplicate Playoffs separator rows "
            f"must be {expected_rows}, found {separator_rows}"
        )
    if expected_rows is None and len(separator_rows) > 1:
        raise ValueError(
            f"{schedule_url}: unverified duplicate Playoffs separator rows "
            f"{separator_rows}"
        )
    return separator_rows


def _without_excluded_remarks(
    frame: pd.DataFrame,
    *,
    policy: HistoricalBrefPolicy | None = None,
) -> pd.DataFrame:
    policy = _policy_or_current(policy)
    remarks = frame["game_remarks"].str.casefold()
    return frame.loc[
        ~remarks.isin(policy.excluded_regular_season_remarks)
    ]


def _verified_duplicate_separator_frame(
    frame: pd.DataFrame,
    schedule_url: str,
    separator_rows: tuple[int, ...],
    *,
    policy: HistoricalBrefPolicy | None = None,
) -> pd.DataFrame:
    policy = _policy_or_current(policy)
    first_separator, second_separator = separator_rows
    output = frame.loc[frame["source_row"] < first_separator]
    regions = dict(
        policy.verified_duplicate_separator_regions
    ).get(schedule_url)
    if regions is None:
        return output
    regular_ids, postseason_ids = regions
    between = frame.loc[
        frame["source_row"].gt(first_separator)
        & frame["source_row"].lt(second_separator)
    ]
    for game_ids, expected_region in (
        (regular_ids, "regular-season"),
        (postseason_ids, "postseason"),
    ):
        for game_id in game_ids:
            matches = frame.loc[frame["game_id"].eq(game_id)]
            if (
                len(matches) != 1
                or not matches["source_row"].between(
                    first_separator + 1, second_separator - 1
                ).all()
            ):
                raise ValueError(
                    f"{schedule_url}: expected {expected_region} game "
                    f"{game_id} exactly once between separators"
                )
    expected_ids = frozenset((*regular_ids, *postseason_ids))
    if len(between) != len(expected_ids) or frozenset(
        between["game_id"]
    ) != expected_ids:
        raise ValueError(
            f"{schedule_url}: between-separator game inventory must be "
            f"exactly {tuple(sorted(expected_ids))}"
        )
    return pd.concat(
        [output, between.loc[between["game_id"].isin(regular_ids)]],
        ignore_index=True,
    )


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


def _validate_cache_root(cache_dir: Path) -> None:
    try:
        mode = cache_dir.lstat().st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(mode):
        raise ValueError(
            f"historical cache root must be a real directory: {cache_dir}"
        )


def fetch(
    raw_dir: Path,
    seasons: Iterable[int] = HISTORICAL_SEASONS,
    sleep_s: float = 12,
) -> None:
    policy = capture_policy()
    schedule_policy = bref_schedule.capture_policy()
    cache_dir = raw_dir / "historical_bref"
    _validate_cache_root(cache_dir)
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
        league = league_for_season(season, policy=policy)
        index_path = cache_dir / index_filename(season, policy=policy)
        page_payload = obtain(
            index_path,
            index_url(season, policy=policy),
            {
                "provider": "basketball_reference",
                "resource": "historical_schedule_index",
                "league": league,
                "season": season,
                "url": index_url(season, policy=policy),
            },
            lambda path, value=season: validate_index_bytes(
                path.read_bytes(), value, policy=policy
            ),
        )
        for page in discover_month_pages(
            page_payload, season, policy=policy
        ):
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
                lambda path, value=season, url=page.url: validate_month_bytes(
                    path.read_bytes(),
                    value,
                    url,
                    policy=policy,
                    schedule_policy=schedule_policy,
                ),
            )


def load_season(raw_dir: Path, season: int) -> pd.DataFrame:
    policy = capture_policy()
    schedule_policy = bref_schedule.capture_policy()
    cache_dir = raw_dir / "historical_bref"
    _validate_cache_root(cache_dir)
    index_path = cache_dir / index_filename(season, policy=policy)
    payload = cache.require_valid(
        index_path,
        lambda path: validate_index_bytes(
            path.read_bytes(), season, policy=policy
        ),
    )
    month_payloads = {}
    for page in discover_month_pages(payload, season, policy=policy):
        month_payloads[page.filename] = cache.require_valid(
            cache_dir / page.filename,
            lambda path, value=season, url=page.url: validate_month_bytes(
                path.read_bytes(),
                value,
                url,
                policy=policy,
                schedule_policy=schedule_policy,
            ),
        )
    return load_season_payloads(
        season=season,
        index_payload=payload,
        month_payloads=month_payloads,
        policy=policy,
        schedule_policy=schedule_policy,
    )


def load_season_payloads(
    *,
    season: int,
    index_payload: bytes,
    month_payloads: Mapping[str, bytes],
    policy: HistoricalBrefPolicy | None = None,
    schedule_policy=None,
) -> pd.DataFrame:
    """Load one historical regular season from captured schedule bytes."""
    policy = _policy_or_current(policy)
    if schedule_policy is None:
        schedule_policy = bref_schedule.capture_policy()
    parsed_pages = []
    separator_found = False
    for page in discover_month_pages(
        index_payload, season, policy=policy
    ):
        try:
            month_payload = month_payloads[page.filename]
        except KeyError as exc:
            raise cache.CacheIntegrityError(
                f"missing captured historical page {page.filename}"
            ) from exc
        validate_month_bytes(
            month_payload,
            season,
            page.url,
            policy=policy,
            schedule_policy=schedule_policy,
        )
        separator_rows = _playoff_separator_source_rows(
            month_payload, page.url, policy=policy
        )
        if separator_found:
            if separator_rows:
                raise ValueError(
                    f"{page.url}: postseason boundary after an earlier "
                    "postseason boundary"
                )
            continue
        frame = parse_schedule_page(
            month_payload,
            season=season,
            schedule_url=page.url,
            policy=policy,
            schedule_policy=schedule_policy,
        )
        if separator_rows:
            if len(separator_rows) == 1:
                selected = frame.loc[
                    frame["source_row"] < separator_rows[0]
                ]
            else:
                selected = _verified_duplicate_separator_frame(
                    frame,
                    page.url,
                    separator_rows,
                    policy=policy,
                )
            selected = _without_excluded_remarks(
                selected, policy=policy
            )
            parsed_pages.append((page, selected))
            separator_found = True
            continue
        parsed_pages.append((
            page,
            _without_excluded_remarks(frame, policy=policy),
        ))
    if not separator_found:
        boundary = dict(policy.curated_playoff_boundaries).get(season)
        if boundary is None:
            raise ValueError(
                f"{season}: no postseason boundary in source or curated data"
            )
        matches = [
            (position, page, frame, source_row)
            for position, (page, frame) in enumerate(parsed_pages)
            for source_row in frame.loc[
                frame["game_id"].eq(boundary.first_playoff_game_id),
                "source_row",
            ]
        ]
        if len(matches) != 1:
            raise ValueError(
                f"{season}: expected one curated postseason boundary target "
                f"{boundary.first_playoff_game_id}, found {len(matches)}"
            )
        position, page, frame, source_row = matches[0]
        if page.url != boundary.schedule_url:
            raise ValueError(
                f"{season}: curated postseason boundary target is in "
                f"{page.url}, expected {boundary.schedule_url}"
            )
        parsed_pages = [
            *parsed_pages[:position],
            (page, frame.loc[frame["source_row"] < source_row]),
        ]
    frames = [frame for _, frame in parsed_pages]
    combined = pd.concat(frames, ignore_index=True)
    combined = _assign_game_ids(combined.drop(columns=["game_id"]))
    return combined.sort_values(
        ["game_date", "game_id"], kind="stable"
    ).reset_index(drop=True)
