"""Fetch and normalize modern regular-season NBA Stats box scores."""

import ipaddress
import json
import socket
import time
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests
from nba_api.stats.endpoints import LeagueGameFinder

from nba_data import cache
from nba_data.team_rows import normalize_team_rows


SourceAccessError = cache.SourceAccessError

SEASONS = {
    2020: "2019-20", 2021: "2020-21", 2022: "2021-22",
    2023: "2022-23", 2024: "2023-24", 2025: "2024-25", 2026: "2025-26",
}
EXPECTED_GAMES = {
    2020: 1059, 2021: 1080, 2022: 1230, 2023: 1230,
    2024: 1230, 2025: 1230, 2026: 1230,
}
REQUIRED = {
    "SEASON_ID", "TEAM_ID", "TEAM_NAME", "GAME_ID", "GAME_DATE", "MATCHUP",
    "WL", "PTS", "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA", "OREB",
    "DREB", "REB", "AST", "STL", "BLK", "TOV", "PF",
}


def preflight_dns() -> tuple[str, ...]:
    """Reject DNS responses that indicate an AdGuard sinkhole."""
    addresses = tuple(sorted({
        result[4][0] for result in socket.getaddrinfo(
            "stats.nba.com", 443, 0, socket.SOCK_STREAM
        )
    }))
    bad = [
        address for address in addresses
        if ipaddress.ip_address(address).is_unspecified
        or ipaddress.ip_address(address).is_loopback
    ]
    if not addresses or bad:
        raise cache.SourceAccessError(
            f"stats.nba.com resolved to {addresses}; check the AdGuard query log "
            "and allowlist @@||stats.nba.com^"
        )
    return addresses


def validate_payload(path: Path) -> None:
    """Confirm that a response is a complete LeagueGameFinder result."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    result = payload["resultSets"][0]
    if result["name"] != "LeagueGameFinderResults":
        raise ValueError(f"unexpected result set {result['name']!r}")
    missing = REQUIRED - set(result["headers"])
    if missing:
        raise ValueError(f"missing LeagueGameFinder columns: {sorted(missing)}")


def fetch(
    raw_dir: Path, seasons: Iterable[int] = SEASONS, sleep_s: float = 12.0
) -> None:
    """Cache one verified LeagueGameFinder response for each requested season."""
    preflight_dns()
    requested = list(seasons)
    for index, season in enumerate(requested):
        destination = raw_dir / "stats_nba" / f"leaguegamefinder_{season}.json"
        if cache.read_valid(destination, validate_payload) is not None:
            continue
        try:
            endpoint = LeagueGameFinder(
                player_or_team_abbreviation="T",
                league_id_nullable="00",
                season_nullable=SEASONS[season],
                season_type_nullable="Regular Season",
                timeout=90,
            )
        except requests.RequestException as exc:
            raise cache.SourceAccessError(
                f"LeagueGameFinder {season} failed: {type(exc).__name__}: {exc}"
            ) from exc
        payload = endpoint.get_json().encode("utf-8")
        cache.publish_bytes(
            destination,
            payload,
            {
                "provider": "stats_nba",
                "endpoint": "LeagueGameFinder",
                "season": season,
                "season_label": SEASONS[season],
            },
            validate_payload,
        )
        if index < len(requested) - 1:
            time.sleep(sleep_s)


def load_season(
    raw_dir: Path, season: int, expected_games: int | None = None
) -> pd.DataFrame:
    """Load a verified cached season and require its full regular-season panel."""
    path = raw_dir / "stats_nba" / f"leaguegamefinder_{season}.json"
    cache.read_valid(path, validate_payload)
    result = json.loads(path.read_text())["resultSets"][0]
    raw = pd.DataFrame(result["rowSet"], columns=result["headers"])
    output = normalize_team_rows(raw, source="nba_stats")
    games = output["game_id"].nunique()
    expected = EXPECTED_GAMES[season] if expected_games is None else expected_games
    if games != expected:
        raise ValueError(f"season {season}: {games} games, expected {expected}")
    if not output["game_id"].str.startswith("002").all():
        raise ValueError(f"season {season}: non-regular NBA GAME_ID found")
    return output
