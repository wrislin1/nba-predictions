import hashlib
import json
import os
from pathlib import Path

import pandas as pd
import pytest
import requests

from nba_data import bref, cache, playoff_bref


FIXTURES = Path(__file__).parent / "fixtures/playoff_bref"


class FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200):
        self.content = content
        self.status_code = status_code


class FakeSession:
    def __init__(self, responses=(), error=None):
        self.responses = iter(responses)
        self.error = error
        self.headers = {}
        self.calls = []

    def get(self, url, *, timeout, verify):
        self.calls.append({"url": url, "timeout": timeout, "verify": verify})
        if self.error is not None:
            raise self.error
        return next(self.responses)


def source_path(raw_dir: Path, season: int) -> Path:
    month = playoff_bref.PLAY_IN_MONTHS[season]
    return raw_dir / "bref" / f"NBA_{season}_games-{month}.html"


def seed_verified_play_in_source(raw_dir: Path, *, season: int) -> bytes:
    path = source_path(raw_dir, season)
    fixture = (
        FIXTURES
        / f"NBA_{season}_games-{playoff_bref.PLAY_IN_MONTHS[season]}.html"
    )
    payload = fixture.read_bytes()
    cache.publish_bytes(
        path,
        payload,
        {
            "provider": "basketball_reference",
            "season": season,
            "month": playoff_bref.PLAY_IN_MONTHS[season],
        },
        bref.validate_payload,
    )
    return payload


def install_session(monkeypatch, session, sleeps):
    monkeypatch.setattr(playoff_bref.requests, "Session", lambda: session)
    monkeypatch.setattr(playoff_bref.time, "sleep", sleeps.append)


def dedicated_page_with_unfinalized_row() -> bytes:
    return (FIXTURES / "NBA_1984_games.html").read_bytes().replace(
        b"</tbody>",
        (
            b"<tr>"
            b"<th data-stat='date_game'>Fri, Apr 20, 1984</th>"
            b"<td data-stat='visitor_pts'></td>"
            b"<td data-stat='home_pts'></td>"
            b"</tr></tbody>"
        ),
        1,
    )


def test_playoff_identity_is_deterministic():
    assert playoff_bref.playoff_url(1984) == (
        "https://www.basketball-reference.com/playoffs/"
        "NBA_1984_games.html"
    )
    assert playoff_bref.playoff_filename(1984) == "NBA_1984_games.html"
    with pytest.raises(ValueError, match="1984..2026"):
        playoff_bref.playoff_url(1983)


def test_modern_union_labels_dedicated_and_play_in_rows(seeded_playoff_cache):
    frame = playoff_bref.load_modern_season(seeded_playoff_cache, 2020)

    assert frame["game_type"].value_counts().to_dict() == {
        "playoff": 1,
        "play_in": 1,
    }
    assert set(frame["game_id"]) == {
        "bref-202008150POR",
        "bref-202008170DEN",
    }
    assert frame["game_id"].is_unique


def test_modern_type_remark_is_non_site_and_dedicated_remark_is_not_play_in(
    seeded_playoff_cache,
):
    frame = playoff_bref.load_modern_season(seeded_playoff_cache, 2020)
    play_in = frame.loc[frame["game_id"].eq("bref-202008150POR")].iloc[0]
    dedicated = frame.loc[frame["game_id"].eq("bref-202008170DEN")].iloc[0]

    assert play_in["game_type"] == "play_in"
    assert play_in["site_status"] == "home"
    assert dedicated["game_type"] == "playoff"


def test_dedicated_play_in_remark_remains_playoff(seeded_playoff_cache):
    dedicated = seeded_playoff_cache / "playoff_bref/NBA_2020_games.html"
    dedicated.unlink()
    dedicated.with_name(dedicated.name + ".meta.json").unlink()
    payload = (FIXTURES / "NBA_2020_games.html").read_bytes().replace(
        b'<td data-stat="game_remarks"></td>',
        b'<td data-stat="game_remarks">Play-In Game</td>',
    )
    cache.publish_bytes(
        dedicated,
        payload,
        {
            "provider": "basketball_reference",
            "resource": "playoff_schedule",
            "league": "NBA",
            "season": 2020,
            "month": None,
            "url": playoff_bref.playoff_url(2020),
        },
        lambda path: playoff_bref.validate_dedicated_page(path, 2020),
    )

    frame = playoff_bref.load_modern_season(seeded_playoff_cache, 2020)
    dedicated_game = frame.loc[
        frame["game_id"].eq("bref-202008170DEN")
    ].iloc[0]

    assert dedicated_game["game_type"] == "playoff"
    assert dedicated_game["site_status"] == "home"


def test_modern_union_rejects_duplicate_natural_results(seeded_playoff_cache):
    dedicated = seeded_playoff_cache / "playoff_bref/NBA_2020_games.html"
    dedicated.unlink()
    dedicated.with_name(dedicated.name + ".meta.json").unlink()
    payload = (FIXTURES / "NBA_2020_games-august.html").read_bytes().replace(
        b"202008150POR.html", b"202008150XXX.html"
    )
    cache.publish_bytes(
        dedicated,
        payload,
        {
            "provider": "basketball_reference",
            "resource": "playoff_schedule",
            "league": "NBA",
            "season": 2020,
            "month": None,
            "url": playoff_bref.playoff_url(2020),
        },
        lambda path: playoff_bref.validate_dedicated_page(path, 2020),
    )

    with pytest.raises(ValueError, match="duplicate result identities"):
        playoff_bref.load_modern_season(seeded_playoff_cache, 2020)


def test_seed_play_in_page_requires_and_preserves_verified_source(tmp_path):
    raw_dir = tmp_path / "raw"
    source = raw_dir / "bref/NBA_2020_games-august.html"
    payload = (FIXTURES / "NBA_2020_games-august.html").read_bytes()
    cache.publish_bytes(
        source,
        payload,
        {
            "provider": "basketball_reference",
            "season": 2020,
            "month": "august",
        },
        bref.validate_payload,
    )

    destination = playoff_bref.seed_play_in_page(raw_dir, 2020)

    assert destination == raw_dir / "playoff_bref/NBA_2020_games-august.html"
    assert destination.read_bytes() == payload
    metadata = json.loads(
        destination.with_name(destination.name + ".meta.json").read_text(
            encoding="utf-8"
        )
    )
    assert metadata["provider"] == "basketball_reference"
    assert metadata["resource"] == "play_in_schedule_month"
    assert metadata["league"] == "NBA"
    assert metadata["season"] == 2020
    assert metadata["month"] == "august"
    assert metadata["url"].endswith("/leagues/NBA_2020_games-august.html")
    assert metadata["seeded_from"] == "raw/bref/NBA_2020_games-august.html"
    assert metadata["seed_operation"] == "verified_cache_copy"
    assert metadata["source_sha256"] == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize(
    "defect",
    ["missing_body", "missing_sidecar", "checksum", "wrong_season", "wrong_month"],
)
def test_seed_play_in_page_rejects_unverified_source(tmp_path, defect):
    raw_dir = tmp_path / "raw"
    source = source_path(raw_dir, 2020)
    seed_verified_play_in_source(raw_dir, season=2020)
    sidecar = source.with_name(source.name + ".meta.json")
    if defect == "missing_body":
        source.unlink()
    elif defect == "missing_sidecar":
        sidecar.unlink()
    elif defect == "checksum":
        source.write_bytes(b"changed")
    else:
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        metadata["season" if defect == "wrong_season" else "month"] = (
            2019 if defect == "wrong_season" else "may"
        )
        sidecar.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises((cache.CacheIntegrityError, ValueError)):
        playoff_bref.seed_play_in_page(raw_dir, 2020)


def test_seed_play_in_page_rejects_destination_symlink_without_following(tmp_path):
    raw_dir = tmp_path / "raw"
    seed_verified_play_in_source(raw_dir, season=2020)
    cache_dir = raw_dir / "playoff_bref"
    cache_dir.mkdir()
    target = tmp_path / "target.html"
    target.write_bytes(b"untouched")
    destination = cache_dir / "NBA_2020_games-august.html"
    destination.symlink_to(target)

    with pytest.raises(cache.CacheIntegrityError, match="regular file"):
        playoff_bref.seed_play_in_page(raw_dir, 2020)

    assert target.read_bytes() == b"untouched"


def test_seed_play_in_page_rejects_nonidentical_existing_destination(tmp_path):
    raw_dir = tmp_path / "raw"
    seed_verified_play_in_source(raw_dir, season=2020)
    destination = playoff_bref.seed_play_in_page(raw_dir, 2020)
    sidecar = destination.with_name(destination.name + ".meta.json")
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    metadata["seed_operation"] = "unverified_copy"
    sidecar.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(cache.CacheIntegrityError, match="existing seeded"):
        playoff_bref.seed_play_in_page(raw_dir, 2020)


def test_seed_play_in_page_ignores_non_play_in_seasons(tmp_path):
    assert playoff_bref.seed_play_in_page(tmp_path / "raw", 2019) is None


def test_dedicated_validator_requires_completed_box_scored_rows(tmp_path):
    path = tmp_path / "NBA_1984_games.html"
    path.write_bytes((FIXTURES / path.name).read_bytes())
    playoff_bref.validate_dedicated_page(path, 1984)

    path.write_bytes(
        path.read_bytes().replace(b'/boxscores/198404170LAL.html', b'', 1)
    )
    with pytest.raises(ValueError, match="invalid box-score href"):
        playoff_bref.validate_dedicated_page(path, 1984)


def test_dedicated_validator_rejects_a_future_row_after_completed_games(tmp_path):
    path = tmp_path / "NBA_1984_games.html"
    path.write_bytes(dedicated_page_with_unfinalized_row())

    with pytest.raises(ValueError, match="unfinalized schedule row"):
        playoff_bref.validate_dedicated_page(path, 1984)


def test_seeded_validator_requires_a_play_in_row(tmp_path):
    path = tmp_path / "NBA_2020_games-august.html"
    path.write_bytes((FIXTURES / path.name).read_bytes())
    playoff_bref.validate_seeded_play_in_page(path, 2020)

    path.write_bytes(path.read_bytes().replace(b"Play-In Game", b"Regular Season"))
    with pytest.raises(ValueError, match="play-in game"):
        playoff_bref.validate_seeded_play_in_page(path, 2020)


def test_fetch_resumes_and_sleeps_only_between_requests(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    seed_verified_play_in_source(raw_dir, season=2020)
    responses = [
        FakeResponse((FIXTURES / name).read_bytes())
        for name in ("NBA_1984_games.html", "NBA_2020_games.html")
    ]
    session = FakeSession(responses)
    sleeps = []
    install_session(monkeypatch, session, sleeps)

    playoff_bref.fetch(raw_dir, seasons=(1984, 2020), sleep_s=12)
    playoff_bref.fetch(raw_dir, seasons=(1984, 2020), sleep_s=12)

    assert [call["verify"] for call in session.calls] == [True, True]
    assert [call["timeout"] for call in session.calls] == [30, 30]
    assert sleeps == [12]
    assert len(session.calls) == 2
    assert session.headers["User-Agent"] == bref.USER_AGENT


def test_fetch_rejects_a_cadence_below_twelve_seconds(tmp_path, monkeypatch):
    session = FakeSession()
    install_session(monkeypatch, session, [])

    with pytest.raises(ValueError, match="at least 12"):
        playoff_bref.fetch(tmp_path / "raw", seasons=(1984,), sleep_s=11.99)

    assert session.calls == []


@pytest.mark.parametrize(
    ("response", "error", "message"),
    [
        (FakeResponse(b"blocked", 403), None, "HTTP 403"),
        (FakeResponse(b"rate limited", 429), None, "HTTP 429"),
        (None, requests.exceptions.SSLError("bad certificate"), "SSLError"),
        (FakeResponse(b""), None, "invalid HTML"),
        (FakeResponse(b"<html></html>"), None, "schedule table"),
        (
            FakeResponse(dedicated_page_with_unfinalized_row()),
            None,
            "unfinalized schedule row",
        ),
        (
            FakeResponse(
                (FIXTURES / "NBA_1984_games.html").read_bytes().replace(
                    b"/boxscores/198404170LAL.html", b"/games/not-a-boxscore", 1
                )
            ),
            None,
            "invalid box-score href",
        ),
    ],
)
def test_fetch_stops_on_first_failed_response_without_publishing(
    tmp_path, monkeypatch, response, error, message
):
    session = FakeSession([response, FakeResponse(b"unused")], error=error)
    install_session(monkeypatch, session, [])

    with pytest.raises((cache.SourceAccessError, ValueError), match=message):
        playoff_bref.fetch(tmp_path / "raw", seasons=(1984, 2020))

    cache_dir = tmp_path / "raw/playoff_bref"
    assert len(session.calls) == 1
    assert not cache_dir.exists() or not list(cache_dir.iterdir())


def test_inventory_accepts_only_verified_contract_entries(tmp_path):
    raw_dir = tmp_path / "raw"
    seed_verified_play_in_source(raw_dir, season=2020)
    playoff_bref.seed_play_in_page(raw_dir, 2020)
    dedicated = raw_dir / "playoff_bref/NBA_1984_games.html"
    cache.publish_bytes(
        dedicated,
        (FIXTURES / dedicated.name).read_bytes(),
        {
            "provider": "basketball_reference",
            "resource": "playoff_schedule",
            "league": "NBA",
            "season": 1984,
            "month": None,
            "url": playoff_bref.playoff_url(1984),
        },
        lambda path: playoff_bref.validate_dedicated_page(path, 1984),
    )

    assert playoff_bref.validate_cache_inventory(raw_dir, complete=False) == (
        dedicated,
        raw_dir / "playoff_bref/NBA_2020_games-august.html",
    )


@pytest.mark.parametrize(
    "entry", ["orphan", "part", "directory", "symlink", "special", "unrelated"]
)
def test_inventory_rejects_non_contract_entries(tmp_path, entry):
    raw_dir = tmp_path / "raw"
    cache_dir = raw_dir / "playoff_bref"
    cache_dir.mkdir(parents=True)
    if entry == "orphan":
        (cache_dir / "NBA_1984_games.html").write_bytes(b"body")
    elif entry == "part":
        (cache_dir / "NBA_1984_games.html.part").write_bytes(b"partial")
    elif entry == "directory":
        (cache_dir / "unexpected").mkdir()
    elif entry == "symlink":
        target = tmp_path / "target.html"
        target.write_bytes(b"body")
        (cache_dir / "NBA_1984_games.html").symlink_to(target)
    elif entry == "special":
        os.mkfifo(cache_dir / "unexpected.fifo")
    else:
        (cache_dir / "unrelated.txt").write_text("nope", encoding="utf-8")

    with pytest.raises(cache.CacheIntegrityError, match="inventory"):
        playoff_bref.validate_cache_inventory(raw_dir, complete=False)


@pytest.mark.integration
def test_full_historical_complement_inventory(repo_data_dir):
    frames = [
        playoff_bref.load_historical_season(repo_data_dir / "raw", season)
        for season in range(1947, 1984)
    ]
    games = pd.concat(frames, ignore_index=True)
    assert len(games) == 1240
    assert games["game_type"].value_counts().to_dict() == {
        "playoff": 1231,
        "tiebreaker": 8,
        "voided": 1,
    }
    assert int(games["game_remarks"].fillna("").ne("").sum()) == 68
    assert int(
        games["game_remarks"]
        .fillna("")
        .str.casefold()
        .str.startswith("at ")
        .sum()
    ) == 44
    assert set(
        games.loc[
            games["game_type"].eq("tiebreaker"), "season"
        ].value_counts().to_dict().items()
    ) == {(1948, 2), (1950, 2), (1956, 2), (1957, 2)}
    voided = games.loc[games["game_type"].eq("voided")].iloc[0]
    assert voided["game_id"] == "bref-195211280MLH"
    assert voided["game_date"].isoformat() == "1952-11-28"
    assert voided["season"] == 1953
