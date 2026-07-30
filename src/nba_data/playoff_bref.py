"""Acquire and validate the dedicated Basketball-Reference playoff cache."""

import hashlib
import json
import re
import stat
from pathlib import Path
import time
from typing import Iterable

import pandas as pd
import requests

from nba_data import bref, bref_schedule, cache


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


def validate_dedicated_page(path: Path, season: int) -> None:
    """Require a completed, box-score-backed dedicated playoff schedule."""
    url = playoff_url(season)
    frame = bref_schedule.parse_schedule_page(
        path.read_bytes(), season=season, league="NBA", schedule_url=url
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
