"""Pin of the sportsdataverse injury-report derivative, for cross-checking only.

``sportsdataverse/sportsdataverse-data`` publishes one Parquet asset per
season (``injury_report_<season>.parquet``) under its
``nba_stats_injury_report`` release.  Those assets are a third-party parse
of the same official PDFs this project parses itself.  They are pinned under
``data/raw/sportsdataverse/nba_stats_injury_report/`` with sidecars that
record the release asset's ``updated_at`` and the body SHA-256, so the build
can compare our listings against theirs version by version.  Agreement is
never a gate and the derivative never resolves a disagreement in its own
favour: it is itself a parse.

This module owns the release binding (``REPOSITORY``, ``RELEASE_TAG``,
``asset_url``), the raw paths and the structural validator, the pin itself
(``fetch``) and the normalisation of one asset into the common comparison
row shape (``comparison_rows``).  The comparison is performed by the build.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Iterable

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests

from nba_data import cache
from nba_data.injury_report_parse import PLACEHOLDER_PHRASES
from nba_data.injury_reports import EASTERN, TIMEOUT_S, USER_AGENT
from nba_data.stats_nba import SourceAccessError


REPOSITORY = "sportsdataverse/sportsdataverse-data"
RELEASE_TAG = "nba_stats_injury_report"
SEASONS = tuple(range(2019, 2027))
RAW_SUBDIR = "raw/sportsdataverse/nba_stats_injury_report"
RELEASE_API_URL = (
    f"https://api.github.com/repos/{REPOSITORY}/releases/tags/{RELEASE_TAG}"
)
PROVIDER = "sportsdataverse"
RESOURCE = "nba_stats_injury_report"
ASSET_COLUMNS = (
    "season", "report_datetime", "game_date", "game_time", "report_matchup",
    "away_team_abbr", "home_team_abbr", "team", "report_player_name",
    "current_status", "reason", "category", "previous_status", "game_id",
    "team_id", "person_id", "match_method",
)
COMPARISON_COLUMNS = (
    "report_datetime", "game_date", "matchup", "team", "kind", "player_name",
    "status", "reason", "category", "previous_status", "artifact",
)

_WHITESPACE = re.compile(r"\s+")


class InjuryDerivativeError(ValueError):
    """A derivative asset or request that fails one of the pin's gates."""


# --------------------------------------------------------------------------
# release binding, paths and validation
# --------------------------------------------------------------------------


def asset_name(season: int) -> str:
    return f"injury_report_{season}.parquet"


def asset_url(season: int) -> str:
    return (
        f"https://github.com/{REPOSITORY}/releases/download/{RELEASE_TAG}/"
        f"{asset_name(season)}"
    )


def asset_path(data_dir: Path, season: int) -> Path:
    return Path(data_dir) / RAW_SUBDIR / asset_name(season)


def _check_columns(names: Iterable[str], *, label: str) -> None:
    observed = tuple(names)
    expected = set(ASSET_COLUMNS)
    seen = set(observed)
    missing = sorted(expected - seen)
    unexpected = sorted(name for name in observed if name not in expected)
    duplicates = sorted(
        name for name in seen if observed.count(name) > 1
    )
    if missing or unexpected or duplicates:
        raise InjuryDerivativeError(
            f"{label}: derivative Parquet must carry exactly the "
            f"{len(ASSET_COLUMNS)} known columns; missing {missing}, "
            f"unexpected {unexpected}, duplicated {duplicates}"
        )


def _read_schema(source, *, label: str) -> pa.Schema:
    try:
        return pq.read_schema(source)
    except (pa.ArrowException, OSError, ValueError) as exc:
        raise InjuryDerivativeError(
            f"{label}: not a readable Parquet file: {type(exc).__name__}: {exc}"
        ) from exc


def validate_asset(path: Path) -> None:
    """Require a Parquet body whose columns are exactly the 17 known ones."""
    path = Path(path)
    schema = _read_schema(path, label=str(path))
    _check_columns(schema.names, label=str(path))


def expected_metadata(season: int) -> dict:
    """The sidecar fields the pin fixes for one season (without the release time)."""
    return {
        "provider": PROVIDER,
        "resource": RESOURCE,
        "season": season,
        "url": asset_url(season),
    }


def read_cached(data_dir: Path, season: int) -> tuple[bytes, dict] | None:
    """Return one stable, attested asset pair, or ``None`` if absent.

    A pair whose sidecar does not carry this season's binding, the release
    asset time or the body length is damaged, and ``CacheIntegrityError`` is
    raised rather than the pair being silently refetched.
    """
    destination = asset_path(data_dir, season)
    attestation = cache.read_attested(destination, validate_asset)
    if attestation is None:
        return None
    payload, metadata = attestation
    expected = expected_metadata(season)
    if any(
        key not in metadata or metadata[key] != value
        for key, value in expected.items()
    ):
        raise cache.CacheIntegrityError(
            f"derivative cache metadata mismatch for {destination}"
        )
    updated_at = metadata.get("release_asset_updated_at")
    if not isinstance(updated_at, str) or not updated_at:
        raise cache.CacheIntegrityError(
            f"derivative cache sidecar lacks release_asset_updated_at for {destination}"
        )
    if metadata.get("content_length") != len(payload):
        raise cache.CacheIntegrityError(
            f"derivative cache content length mismatch for {destination}"
        )
    return payload, metadata


# --------------------------------------------------------------------------
# fetch
# --------------------------------------------------------------------------


def _selected_seasons(seasons: Iterable[int] | None) -> tuple[int, ...]:
    if seasons is None:
        return SEASONS
    selected = tuple(sorted(set(int(season) for season in seasons)))
    outside = [season for season in selected if season not in SEASONS]
    if outside:
        raise InjuryDerivativeError(
            f"seasons {outside} are outside the derivative scope "
            f"{SEASONS[0]}-{SEASONS[-1]}"
        )
    return selected


def _release_assets(session) -> dict[str, dict]:
    """Read the release once and index its assets by file name."""
    try:
        response = session.get(
            RELEASE_API_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": USER_AGENT,
            },
            timeout=TIMEOUT_S,
        )
    except requests.RequestException as exc:
        raise SourceAccessError(
            f"release {RELEASE_TAG}: {type(exc).__name__}: {exc}"
        ) from exc
    if response.status_code != 200:
        raise SourceAccessError(
            f"release {RELEASE_TAG}: HTTP {response.status_code} "
            f"({len(response.content)} bytes)"
        )
    try:
        release = json.loads(response.content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceAccessError(
            f"release {RELEASE_TAG}: body is not JSON: {exc}"
        ) from exc
    assets = release.get("assets") if isinstance(release, dict) else None
    if not isinstance(assets, list):
        raise SourceAccessError(
            f"release {RELEASE_TAG}: body carries no asset list"
        )
    indexed: dict[str, dict] = {}
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("name"), str):
            raise SourceAccessError(
                f"release {RELEASE_TAG}: malformed asset entry {asset!r}"
            )
        indexed[asset["name"]] = asset
    return indexed


def _download_asset(session, season: int) -> bytes:
    url = asset_url(season)
    try:
        response = session.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT_S,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        raise SourceAccessError(
            f"asset {asset_name(season)}: {type(exc).__name__}: {exc}"
        ) from exc
    if response.status_code != 200:
        raise SourceAccessError(
            f"asset {asset_name(season)}: HTTP {response.status_code} "
            f"({len(response.content)} bytes)"
        )
    return response.content


def fetch(
    data_dir: Path,
    seasons: Iterable[int] | None = None,
    *,
    session: requests.Session | None = None,
) -> None:
    """Pin every uncached season asset of the derivative release.

    A complete valid pair is skipped; a damaged pair raises
    ``CacheIntegrityError`` before any request is sent; a season outside
    ``SEASONS`` is refused.  Once at least one season is pending the release
    API is read exactly once, and every pending season must have its asset
    in that release or ``SourceAccessError`` is raised before any download
    (fail closed: a season never silently drops out of the pin).  Each
    pending asset is then downloaded (following GitHub's redirect to the
    asset host) and published atomically through ``cache.publish_bytes``
    with ``validate_asset``, its sidecar carrying the provider, resource,
    season, URL and the release asset's ``updated_at``.

    TODO(task-19): the real pin of the eight assets runs on the owner's
    machine in plan Task 19; nothing under
    ``data/raw/sportsdataverse/nba_stats_injury_report/`` exists in this
    checkout, and the ``report_datetime`` storage (naive Eastern against
    an aware instant) is confirmed there against the cached 2026 asset.
    """
    data_dir = Path(data_dir)
    selected = _selected_seasons(seasons)
    pending = [
        season for season in selected if read_cached(data_dir, season) is None
    ]
    if not pending:
        return

    session = session if session is not None else requests.Session()
    assets = _release_assets(session)
    missing = [
        asset_name(season) for season in pending if asset_name(season) not in assets
    ]
    if missing:
        raise SourceAccessError(
            f"release {RELEASE_TAG} of {REPOSITORY} lacks assets {missing}"
        )
    for season in pending:
        asset = assets[asset_name(season)]
        updated_at = asset.get("updated_at")
        if not isinstance(updated_at, str) or not updated_at:
            raise SourceAccessError(
                f"release asset {asset_name(season)} carries no updated_at"
            )
        payload = _download_asset(session, season)
        cache.publish_bytes(
            asset_path(data_dir, season),
            payload,
            {**expected_metadata(season), "release_asset_updated_at": updated_at},
            validate_asset,
        )


# --------------------------------------------------------------------------
# normalisation into comparison rows
# --------------------------------------------------------------------------


def _collapse(value) -> str | None:
    """Collapse internal whitespace and strip; null and empty become ``None``."""
    if (
        value is None
        or value is pd.NA
        or (isinstance(value, float) and pd.isna(value))
    ):
        return None
    if not isinstance(value, str):
        value = str(value)
    collapsed = _WHITESPACE.sub(" ", value).strip()
    return collapsed or None


def _naive_eastern(series: pd.Series) -> pd.Series:
    """``report_datetime`` as naive Eastern wall time.

    An aware column is converted to ``America/New_York`` and stripped of its
    zone; a naive column is taken as Eastern wall time already; strings are
    parsed first.
    """
    if not pd.api.types.is_datetime64_any_dtype(series):
        series = pd.to_datetime(series, errors="raise")
    if getattr(series.dt, "tz", None) is not None:
        series = series.dt.tz_convert(EASTERN).dt.tz_localize(None)
    return series.astype("datetime64[ns]")


def _game_dates(series: pd.Series) -> list:
    parsed = pd.to_datetime(series, errors="raise")
    if getattr(parsed.dt, "tz", None) is not None:
        parsed = parsed.dt.tz_localize(None)
    return [None if pd.isna(value) else value.date() for value in parsed]


def comparison_rows(payload: bytes) -> pd.DataFrame:
    """Map one derivative asset into the common comparison row shape.

    Text columns have internal whitespace collapsed.  A row with null
    ``report_player_name`` and null ``current_status`` whose ``reason`` is
    one of ``PLACEHOLDER_PHRASES`` is a placeholder: ``kind`` holds the
    phrase and ``player_name``, ``status`` and ``reason`` are null.  Such a
    row whose ``reason`` is not a phrase (the ``,`` rows) keeps its fields
    and is marked ``artifact``; every other row is ``kind`` ``player``.
    """
    source = io.BytesIO(payload)
    _check_columns(_read_schema(source, label="derivative payload").names,
                   label="derivative payload")
    source.seek(0)
    table = pq.read_table(source)
    frame = table.to_pandas(types_mapper=None)

    text = {
        name: [_collapse(value) for value in frame[name].tolist()]
        for name in (
            "report_matchup", "team", "report_player_name", "current_status",
            "reason", "category", "previous_status",
        )
    }
    kinds: list[str] = []
    artifacts: list[bool] = []
    player_names: list[str | None] = []
    statuses: list[str | None] = []
    reasons: list[str | None] = []
    for name, status, reason in zip(
        text["report_player_name"], text["current_status"], text["reason"]
    ):
        if name is None and status is None:
            if reason in PLACEHOLDER_PHRASES:
                kinds.append(reason)
                artifacts.append(False)
                player_names.append(None)
                statuses.append(None)
                reasons.append(None)
                continue
            artifacts.append(True)
        else:
            artifacts.append(False)
        kinds.append("player")
        player_names.append(name)
        statuses.append(status)
        reasons.append(reason)

    rows = pd.DataFrame({
        "report_datetime": _naive_eastern(frame["report_datetime"]).to_numpy(),
        "game_date": pd.Series(_game_dates(frame["game_date"]), dtype=object),
        "matchup": pd.Series(text["report_matchup"], dtype=object),
        "team": pd.Series(text["team"], dtype=object),
        "kind": pd.Series(kinds, dtype=object),
        "player_name": pd.Series(player_names, dtype=object),
        "status": pd.Series(statuses, dtype=object),
        "reason": pd.Series(reasons, dtype=object),
        "category": pd.Series(text["category"], dtype=object),
        "previous_status": pd.Series(text["previous_status"], dtype=object),
        "artifact": pd.Series(artifacts, dtype=bool),
    }, columns=list(COMPARISON_COLUMNS))
    return rows.reset_index(drop=True)
