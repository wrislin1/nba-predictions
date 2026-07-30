import hashlib
import json
import os
from pathlib import Path

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


def test_playoff_identity_is_deterministic():
    assert playoff_bref.playoff_url(1984) == (
        "https://www.basketball-reference.com/playoffs/"
        "NBA_1984_games.html"
    )
    assert playoff_bref.playoff_filename(1984) == "NBA_1984_games.html"
    with pytest.raises(ValueError, match="1984..2026"):
        playoff_bref.playoff_url(1983)


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


@pytest.mark.parametrize(
    ("response", "error", "message"),
    [
        (FakeResponse(b"blocked", 403), None, "HTTP 403"),
        (FakeResponse(b"rate limited", 429), None, "HTTP 429"),
        (None, requests.exceptions.SSLError("bad certificate"), "SSLError"),
        (FakeResponse(b"<html></html>"), None, "schedule table"),
        (
            FakeResponse(
                (FIXTURES / "NBA_1984_games.html").read_bytes().replace(
                    b">113<", b"><", 1
                )
            ),
            None,
            "invalid literal",
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
