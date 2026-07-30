"""Acquire and validate the dedicated Basketball-Reference playoff cache."""

import hashlib
import json
import re
import stat
from pathlib import Path
import time
from typing import Iterable

from lxml import etree, html
import pandas as pd
import requests

from nba_data import bref, bref_schedule, cache, historical_bref


BASE_URL = "https://www.basketball-reference.com"
PLAYOFF_SEASONS = tuple(range(1947, 2027))
DEDICATED_SEASONS = tuple(range(1984, 2027))
PLAY_IN_MONTHS = {
    2020: "august",
    2021: "may",
    **{season: "april" for season in range(2022, 2027)},
}

_DEDICATED_NAME = re.compile(r"^NBA_(\d{4})_games\.html$")
_SEEDED_NAME = re.compile(r"^NBA_(\d{4})_games-([a-z]+)\.html$")

HISTORICAL_NEUTRAL_REMARKS = frozenset({
    "neutral site",
    "at saint paul, mn",
    "at 69th regiment armory",
    "at madison, wi",
    "at indianapolis, in",
    "at oakland, ca",
    "at chicago, il (chicago arena)",
    "at washington, dc (national guard armory)",
    "at daly city, ca",
    "at kingdome",
    "at elkhart, in",
    "at detroit, mi (university of detroit fieldhouse)",
    "at tempe, az",
})
HISTORICAL_NONSITE_REMARKS = frozenset({
    "round robin",
    "havlicek stole the ball",
    "the willis reed game",
    "the greatest game ever played",
    "mother's day massacre",
})
TYPE_REMARKS = frozenset({
    "play-in game",
    "tiebreaker",
    "protest; does not count toward record",
})


def playoff_url(season: int) -> str:
    if season not in DEDICATED_SEASONS:
        raise ValueError(
            f"dedicated playoff season must be 1984..2026: {season}"
        )
    return f"{BASE_URL}/playoffs/NBA_{season}_games.html"


def playoff_filename(season: int) -> str:
    playoff_url(season)
    return f"NBA_{season}_games.html"


def _play_in_filename(season: int) -> str:
    return f"NBA_{season}_games-{PLAY_IN_MONTHS[season]}.html"


def _play_in_url(season: int) -> str:
    month = PLAY_IN_MONTHS[season]
    return f"{BASE_URL}/leagues/NBA_{season}_games-{month}.html"


def _validate_frame(frame: pd.DataFrame, *, label: str) -> None:
    if frame.empty:
        raise ValueError(f"{label}: expected at least one completed row")
    if frame[["away_pts", "home_pts"]].isna().any().any():
        raise ValueError(f"{label}: completed rows must have both scores")
    boxscore_url = frame["boxscore_url"]
    game_id = frame["game_id"].astype("string")
    valid_ids = game_id.str.startswith("bref-", na=False)
    if boxscore_url.isna().any() or not bool(valid_ids.all()):
        raise ValueError(f"{label}: every row must be box-score-backed")


def _cell_text(row, stat_name: str) -> str:
    cells = row.xpath(
        f"./th[@data-stat='{stat_name}'] | "
        f"./td[@data-stat='{stat_name}']"
    )
    if not cells:
        return ""
    return " ".join("".join(cells[0].itertext()).split())


def _reject_unfinalized_rows(payload: bytes, *, url: str) -> None:
    try:
        root = html.fromstring(payload)
    except (TypeError, ValueError, etree.ParserError) as exc:
        raise ValueError(f"{url}: invalid HTML") from exc
    tables = root.xpath("//table[@id='schedule']")
    if len(tables) != 1:
        return
    for source_row, row in enumerate(tables[0].xpath("./tbody/tr"), start=1):
        if not _cell_text(row, "date_game"):
            continue
        away_pts = _cell_text(row, "visitor_pts")
        home_pts = _cell_text(row, "home_pts")
        if not away_pts and not home_pts:
            raise ValueError(
                f"{url}: source row {source_row} is an unfinalized schedule row"
            )


def validate_dedicated_page(path: Path, season: int) -> None:
    """Require a completed, box-score-backed dedicated playoff schedule."""
    url = playoff_url(season)
    payload = path.read_bytes()
    _reject_unfinalized_rows(payload, url=url)
    frame = bref_schedule.parse_schedule_page(
        payload, season=season, league="NBA", schedule_url=url
    )
    _validate_frame(frame, label=url)


def validate_seeded_play_in_page(path: Path, season: int) -> None:
    """Require a completed play-in row in its verified regular-month page."""
    if season not in PLAY_IN_MONTHS:
        raise ValueError(f"season has no play-in month: {season}")
    url = _play_in_url(season)
    frame = bref_schedule.parse_schedule_page(
        path.read_bytes(), season=season, league="NBA", schedule_url=url
    )
    _validate_frame(frame, label=url)
    remarks = frame["game_remarks"].astype("string").str.strip().str.casefold()
    if not bool(remarks.eq("play-in game").any()):
        raise ValueError(f"{url}: expected a completed play-in game row")


def _source_keys(frame: pd.DataFrame) -> pd.Series:
    return frame[["schedule_url", "source_row"]].apply(tuple, axis=1)


def _require_unique(frame: pd.DataFrame, *, label: str) -> None:
    source_keys = _source_keys(frame)
    if bool(source_keys.duplicated().any()):
        raise ValueError(f"{label}: duplicate source keys")
    if bool(frame["game_id"].duplicated().any()):
        raise ValueError(f"{label}: duplicate game IDs")


def _require_unique_results(frame: pd.DataFrame, *, label: str) -> None:
    result_columns = [
        "game_date", "away_team_name", "home_team_name", "away_pts", "home_pts",
    ]
    if bool(frame.duplicated(result_columns).any()):
        raise ValueError(f"{label}: duplicate result identities")


def _normalize_type_remarks(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    normalized = result["game_remarks"].astype("string").str.strip().str.casefold()
    result.loc[
        normalized.isin(TYPE_REMARKS) & result["site_status"].eq("unknown"),
        "site_status",
    ] = "home"
    return result


def classify_historical_complement(frame: pd.DataFrame) -> pd.DataFrame:
    """Classify the audited historical selector complement."""
    result = frame.copy()
    normalized = result["game_remarks"].astype("string").str.strip().str.casefold()
    neutral = normalized.isin(HISTORICAL_NEUTRAL_REMARKS)
    nonsite = normalized.isin(HISTORICAL_NONSITE_REMARKS)
    tiebreaker = normalized.eq("tiebreaker")
    voided = normalized.eq("protest; does not count toward record")
    blank = normalized.isna() | normalized.eq("")
    recognized = neutral | nonsite | tiebreaker | voided | blank
    invalid = normalized.notna() & ~recognized
    if bool(invalid.any()):
        raise ValueError(
            "unrecognized scoped game remark: "
            f"{normalized.loc[invalid].iloc[0]}"
        )
    result["game_type"] = "playoff"
    result.loc[tiebreaker, "game_type"] = "tiebreaker"
    result.loc[voided, "game_type"] = "voided"
    result.loc[neutral, "site_status"] = "neutral"
    result.loc[
        (nonsite | tiebreaker | voided) & result["site_status"].eq("unknown"),
        "site_status",
    ] = "home"
    return result


def load_historical_season(raw_dir: Path, season: int) -> pd.DataFrame:
    """Load 1947–1983 games absent from the proven regular-season selector."""
    regular = historical_bref.load_season(raw_dir, season)
    cache_dir = raw_dir / "historical_bref"
    index_payload = cache.require_valid(
        cache_dir / historical_bref.index_filename(season),
        lambda path: historical_bref.validate_index(path, season),
    )
    frames = []
    for page in historical_bref.discover_month_pages(index_payload, season):
        payload = cache.require_valid(
            cache_dir / page.filename,
            lambda path, value=season, url=page.url: historical_bref.validate_month(
                path, value, url
            ),
        )
        frames.append(bref_schedule.parse_schedule_page(
            payload,
            season=season,
            league=historical_bref.league_for_season(season),
            schedule_url=page.url,
        ))
    all_rows = bref_schedule.assign_game_ids(
        pd.concat(frames, ignore_index=True).drop(columns=["game_id"])
    )
    _require_unique(regular, label=f"{season} regular selector")
    _require_unique(all_rows, label=f"{season} historical schedule")
    regular_keys = set(_source_keys(regular))
    complement = all_rows.loc[~_source_keys(all_rows).isin(regular_keys)].copy()
    complement_keys = set(_source_keys(complement))
    if regular_keys & complement_keys:
        raise ValueError(f"{season}: regular and postseason source keys overlap")
    complement = classify_historical_complement(complement)
    complement = bref_schedule.assign_game_ids(complement.drop(columns=["game_id"]))
    _require_unique(complement, label=f"{season} historical complement")
    return complement.sort_values(["game_date", "game_id"], kind="stable").reset_index(
        drop=True
    )


def load_modern_season(raw_dir: Path, season: int) -> pd.DataFrame:
    """Load dedicated playoff rows and the approved modern play-in supplement."""
    if season not in DEDICATED_SEASONS:
        raise ValueError(f"modern playoff season must be 1984..2026: {season}")
    cache_dir = raw_dir / "playoff_bref"
    dedicated_path = cache_dir / playoff_filename(season)
    dedicated = bref_schedule.parse_schedule_page(
        cache.require_valid(
            dedicated_path,
            lambda path: validate_dedicated_page(path, season),
        ),
        season=season,
        league="NBA",
        schedule_url=playoff_url(season),
    )
    dedicated["game_type"] = "playoff"
    frames = [_normalize_type_remarks(dedicated)]
    if season in PLAY_IN_MONTHS:
        play_in_path = cache_dir / _play_in_filename(season)
        seeded = bref_schedule.parse_schedule_page(
            cache.require_valid(
                play_in_path,
                lambda path: validate_seeded_play_in_page(path, season),
            ),
            season=season,
            league="NBA",
            schedule_url=_play_in_url(season),
        )
        normalized = seeded["game_remarks"].astype("string").str.strip().str.casefold()
        play_in = seeded.loc[normalized.eq("play-in game")].copy()
        play_in["game_type"] = "play_in"
        frames.append(_normalize_type_remarks(play_in))
    combined = pd.concat(frames, ignore_index=True)
    combined = bref_schedule.assign_game_ids(combined.drop(columns=["game_id"]))
    _require_unique(combined, label=f"{season} modern postseason")
    _require_unique_results(combined, label=f"{season} modern postseason")
    return combined.sort_values(["game_date", "game_id"], kind="stable").reset_index(
        drop=True
    )


def load_season(raw_dir: Path, season: int) -> pd.DataFrame:
    if season in historical_bref.HISTORICAL_SEASONS:
        return load_historical_season(raw_dir, season)
    if season in DEDICATED_SEASONS:
        return load_modern_season(raw_dir, season)
    raise ValueError(f"playoff season must be 1947..2026: {season}")


def _ensure_real_directory(path: Path, *, label: str) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(mode):
        raise ValueError(f"{label} must be a real directory: {path}")


def _ensure_regular(path: Path, *, label: str) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISREG(mode):
        raise cache.CacheIntegrityError(f"{label} must be a regular file: {path}")


def _seed_metadata(season: int, payload: bytes) -> dict:
    month = PLAY_IN_MONTHS[season]
    return {
        "provider": "basketball_reference",
        "resource": "play_in_schedule_month",
        "league": "NBA",
        "season": season,
        "month": month,
        "url": _play_in_url(season),
        "seeded_from": f"raw/bref/NBA_{season}_games-{month}.html",
        "seed_operation": "verified_cache_copy",
        "source_sha256": hashlib.sha256(payload).hexdigest(),
    }


def _metadata(path: Path) -> dict:
    return json.loads(
        path.with_name(path.name + ".meta.json").read_text(encoding="utf-8")
    )


def _remove_parts(path: Path) -> None:
    path.with_name(path.name + ".part").unlink(missing_ok=True)
    path.with_name(path.name + ".meta.json.part").unlink(missing_ok=True)


def seed_play_in_page(raw_dir: Path, season: int) -> Path | None:
    """Copy a checksum-verified regular-month play-in source into this cache."""
    if season not in PLAY_IN_MONTHS:
        return None
    month = PLAY_IN_MONTHS[season]
    source = raw_dir / "bref" / f"NBA_{season}_games-{month}.html"
    source_sidecar = source.with_name(source.name + ".meta.json")
    _ensure_regular(source, label="play-in source body")
    _ensure_regular(source_sidecar, label="play-in source sidecar")
    payload = cache.require_valid(source, bref.validate_payload)
    source_metadata = _metadata(source)
    expected_source = {
        "provider": "basketball_reference",
        "season": season,
        "month": month,
    }
    if any(source_metadata.get(key) != value for key, value in expected_source.items()):
        raise cache.CacheIntegrityError(
            f"play-in source metadata does not identify {source}"
        )

    cache_dir = raw_dir / "playoff_bref"
    _ensure_real_directory(cache_dir, label="playoff cache root")
    destination = cache_dir / _play_in_filename(season)
    sidecar = destination.with_name(destination.name + ".meta.json")
    _ensure_regular(destination, label="seeded destination body")
    _ensure_regular(sidecar, label="seeded destination sidecar")
    expected = _seed_metadata(season, payload)
    if destination.exists() or sidecar.exists():
        cache.require_valid(
            destination,
            lambda path: validate_seeded_play_in_page(path, season),
        )
        destination_metadata = _metadata(destination)
        if (
            any(destination_metadata.get(key) != value for key, value in expected.items())
            or destination_metadata.get("sha256") != hashlib.sha256(payload).hexdigest()
        ):
            raise cache.CacheIntegrityError(
                f"existing seeded cache metadata differs for {destination}"
            )
        return destination
    try:
        return cache.publish_bytes(
            destination,
            payload,
            expected,
            lambda path: validate_seeded_play_in_page(path, season),
        )
    except Exception:
        _remove_parts(destination)
        raise


def _contract_for_body(path: Path) -> tuple[int, str] | None:
    dedicated = _DEDICATED_NAME.fullmatch(path.name)
    if dedicated is not None:
        season = int(dedicated.group(1))
        if season in DEDICATED_SEASONS:
            return season, "dedicated"
        return None
    seeded = _SEEDED_NAME.fullmatch(path.name)
    if seeded is not None:
        season, month = int(seeded.group(1)), seeded.group(2)
        if PLAY_IN_MONTHS.get(season) == month:
            return season, "seeded"
    return None


def _validate_inventory_entry(path: Path, season: int, kind: str) -> None:
    _ensure_regular(path, label="playoff cache body")
    sidecar = path.with_name(path.name + ".meta.json")
    _ensure_regular(sidecar, label="playoff cache sidecar")
    if kind == "dedicated":
        validator = lambda value: validate_dedicated_page(value, season)
        expected = {
            "provider": "basketball_reference",
            "resource": "playoff_schedule",
            "league": "NBA",
            "season": season,
            "month": None,
            "url": playoff_url(season),
        }
    else:
        validator = lambda value: validate_seeded_play_in_page(value, season)
        payload = cache.require_valid(path, validator)
        expected = _seed_metadata(season, payload)
        metadata = _metadata(path)
        if any(metadata.get(key) != value for key, value in expected.items()):
            raise cache.CacheIntegrityError(
                f"playoff cache inventory metadata mismatch for {path}"
            )
        return
    cache.require_valid(path, validator)
    metadata = _metadata(path)
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise cache.CacheIntegrityError(
            f"playoff cache inventory metadata mismatch for {path}"
        )


def validate_cache_inventory(raw_dir: Path, *, complete: bool) -> tuple[Path, ...]:
    """Fail closed unless every cache entry matches a known playoff contract."""
    cache_dir = raw_dir / "playoff_bref"
    _ensure_real_directory(cache_dir, label="playoff cache root")
    if not cache_dir.exists():
        if complete:
            raise cache.CacheIntegrityError("playoff cache inventory is incomplete")
        return ()
    entries = list(cache_dir.iterdir())
    bodies = []
    sidecars = set()
    for entry in entries:
        try:
            mode = entry.lstat().st_mode
        except FileNotFoundError:
            raise cache.CacheIntegrityError(
                f"playoff cache inventory changed while reading: {entry}"
            ) from None
        if not stat.S_ISREG(mode):
            raise cache.CacheIntegrityError(
                f"playoff cache inventory contains non-regular entry: {entry}"
            )
        if entry.name.endswith(".html.meta.json"):
            body_name = entry.name.removesuffix(".meta.json")
            if _contract_for_body(cache_dir / body_name) is None:
                raise cache.CacheIntegrityError(
                    f"playoff cache inventory contains unrelated sidecar: {entry}"
                )
            sidecars.add(body_name)
            continue
        contract = _contract_for_body(entry)
        if contract is None:
            raise cache.CacheIntegrityError(
                f"playoff cache inventory contains unrelated entry: {entry}"
            )
        bodies.append((entry, *contract))
    body_names = {path.name for path, _, _ in bodies}
    if sidecars != body_names:
        raise cache.CacheIntegrityError("playoff cache inventory contains orphan entries")
    for path, season, kind in bodies:
        _validate_inventory_entry(path, season, kind)
    actual = {path.name for path, _, _ in bodies}
    if complete:
        expected_bodies = {
            *(playoff_filename(season) for season in DEDICATED_SEASONS),
            *(
                f"NBA_{season}_games-{month}.html"
                for season, month in PLAY_IN_MONTHS.items()
            ),
        }
        if len(expected_bodies) != 50 or actual != expected_bodies:
            raise cache.CacheIntegrityError("playoff cache inventory is incomplete")
    return tuple(sorted((path for path, _, _ in bodies), key=lambda path: path.name))


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
    seasons: Iterable[int] = DEDICATED_SEASONS,
    sleep_s: float = 12,
) -> None:
    """Acquire missing dedicated pages, after verifying any play-in source."""
    if sleep_s < 12:
        raise ValueError("sleep_s must be at least 12 seconds")
    cache_dir = raw_dir / "playoff_bref"
    _ensure_real_directory(cache_dir, label="playoff cache root")
    validate_cache_inventory(raw_dir, complete=False)
    session = requests.Session()
    session.headers.update({"User-Agent": bref.USER_AGENT})
    requested = False
    for season in tuple(seasons):
        url = playoff_url(season)
        destination = cache_dir / playoff_filename(season)
        seed_play_in_page(raw_dir, season)
        if cache.read_valid(
            destination,
            lambda path, value=season: validate_dedicated_page(path, value),
        ) is not None:
            continue
        if requested:
            time.sleep(sleep_s)
        payload = _request(session, url)
        try:
            cache.publish_bytes(
                destination,
                payload,
                {
                    "provider": "basketball_reference",
                    "resource": "playoff_schedule",
                    "league": "NBA",
                    "season": season,
                    "month": None,
                    "url": url,
                },
                lambda path, value=season: validate_dedicated_page(path, value),
            )
        except Exception:
            _remove_parts(destination)
            raise
        requested = True
