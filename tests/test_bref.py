import datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import requests

from nba_data import bref, cache


RAW = Path("data/raw")
SCHEDULE_HTML = """
<table id="schedule">
  <thead><tr>
    <th>Date</th><th>Start</th><th>Away</th><th>Away PTS</th>
    <th>Home</th><th>Home PTS</th><th>Box</th><th>OT</th>
    <th>Attendance</th><th>Duration</th><th>Arena</th><th>Notes</th>
  </tr></thead>
  <tbody>
    <tr><th>Tue, Dec 22, 2020</th><td>7:00p</td><td>Golden State Warriors</td><td>99</td><td>Brooklyn Nets</td><td>125</td><td>Box Score</td><td></td><td>0</td><td>2:14</td><td>Barclays Center</td><td></td></tr>
    <tr><th>Wed, Dec 23, 2020</th><td>7:00p</td><td>Los Angeles Clippers</td><td>116</td><td>Los Angeles Lakers</td><td>109</td><td>Box Score</td><td></td><td>300</td><td>2:28</td><td>STAPLES Center</td><td></td></tr>
    <tr><th>Fri, Dec 25, 2020</th><td>5:00p</td><td>Miami Heat</td><td>98</td><td>New Orleans Pelicans</td><td>111</td><td>Box Score</td><td></td><td></td><td>2:17</td><td>Smoothie King Center</td><td></td></tr>
    <tr><th>Sun, Dec 27, 2020</th><td></td><td>Houston Rockets</td><td></td><td>Oklahoma City Thunder</td><td></td><td></td><td></td><td></td><td></td><td>Paycom Center</td><td>Postponed</td></tr>
  </tbody>
</table>
"""


def publish_schedule(tmp_path, month="december"):
    destination = tmp_path / f"bref/NBA_2021_games-{month}.html"
    cache.publish_bytes(
        destination,
        SCHEDULE_HTML.encode(),
        {"provider": "basketball_reference", "season": 2021, "month": month},
        bref.validate_payload,
    )


def test_load_attendance_preserves_reported_zero_positive_and_blank(tmp_path):
    """Would fail if completed-row attendance semantics were collapsed or lost."""
    publish_schedule(tmp_path)

    result = bref.load_attendance(tmp_path, 2021, months=["december"])

    assert len(result) == 3
    assert result["attendance"].tolist()[:2] == [0, 300]
    assert pd.isna(result.iloc[2]["attendance"])
    assert result["game_date"].map(type).eq(datetime.date).all()


def test_load_attendance_reports_missing_cache_entry(tmp_path):
    """Would fail if an absent cache leaked a pandas FileNotFoundError."""
    with pytest.raises(
        cache.CacheIntegrityError,
        match="missing cache entry",
    ):
        bref.load_attendance(tmp_path, 2021, months=["december"])


class FakeResponse:
    def __init__(self, content, status_code=200):
        self.content = content
        self.status_code = status_code


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.headers = {}
        self.calls = []

    def get(self, url, *, timeout, verify):
        self.calls.append((url, timeout, verify))
        if self.error is not None:
            raise self.error
        return self.response


def cache_all_but(tmp_path, *missing_months):
    for month in bref.MONTHS_BY_SEASON[2021]:
        if month not in missing_months:
            publish_schedule(tmp_path, month)


def install_fetch_doubles(monkeypatch, session, sleeps):
    monkeypatch.setattr(
        bref,
        "requests",
        SimpleNamespace(Session=lambda: session, RequestException=requests.RequestException),
        raising=False,
    )
    monkeypatch.setattr(
        bref, "time", SimpleNamespace(sleep=sleeps.append), raising=False
    )


def test_fetch_caches_validated_missing_page(tmp_path, monkeypatch):
    """Would fail if a successful Basketball-Reference page bypassed validation/cache."""
    cache_all_but(tmp_path, "december")
    session = FakeSession(FakeResponse(SCHEDULE_HTML.encode()))
    sleeps = []
    install_fetch_doubles(monkeypatch, session, sleeps)

    bref.fetch(tmp_path, [2021])

    destination = tmp_path / "bref/NBA_2021_games-december.html"
    assert cache.read_valid(destination, bref.validate_payload) == SCHEDULE_HTML.encode()
    assert session.calls == [(
        "https://www.basketball-reference.com/leagues/"
        "NBA_2021_games-december.html", 30, True,
    )]
    assert session.headers["User-Agent"] == bref.USER_AGENT
    assert sleeps == []


def test_fetch_cache_hits_do_not_create_requests_or_sleeps(tmp_path, monkeypatch):
    """Would fail if a valid cached page triggered a request or rate-limit delay."""
    cache_all_but(tmp_path)
    session = FakeSession()
    sleeps = []
    install_fetch_doubles(monkeypatch, session, sleeps)

    bref.fetch(tmp_path, [2021])

    assert session.calls == []
    assert sleeps == []


def test_fetch_sleeps_only_between_real_requests(tmp_path, monkeypatch):
    """Would fail if cache hits were counted as requests for rate-limit cadence."""
    cache_all_but(tmp_path, "december", "january")
    session = FakeSession(FakeResponse(SCHEDULE_HTML.encode()))
    sleeps = []
    install_fetch_doubles(monkeypatch, session, sleeps)

    bref.fetch(tmp_path, [2021], sleep_s=0.25)

    assert [call[0].rsplit("-", 1)[-1] for call in session.calls] == [
        "december.html", "january.html",
    ]
    assert sleeps == [0.25]


def test_fetch_wraps_tls_failures_and_does_not_publish(tmp_path, monkeypatch):
    """Would fail if TLS request errors leaked or produced an unverified cache file."""
    cache_all_but(tmp_path, "december")
    session = FakeSession(error=requests.exceptions.SSLError("certificate verify failed"))
    install_fetch_doubles(monkeypatch, session, [])

    with pytest.raises(cache.SourceAccessError, match="SSLError"):
        bref.fetch(tmp_path, [2021])

    assert not (tmp_path / "bref/NBA_2021_games-december.html").exists()


def test_fetch_rejects_bad_schedule_schema_without_publishing(tmp_path, monkeypatch):
    """Would fail if a malformed source response entered the raw cache."""
    cache_all_but(tmp_path, "december")
    malformed = b"<table id='schedule'><tr><th>only column</th></tr><tr><td>x</td></tr></table>"
    session = FakeSession(FakeResponse(malformed))
    install_fetch_doubles(monkeypatch, session, [])

    with pytest.raises(ValueError, match="expected 12 schedule columns"):
        bref.fetch(tmp_path, [2021])

    assert not (tmp_path / "bref/NBA_2021_games-december.html").exists()


@pytest.mark.integration
def test_spike_december_2020_semantics():
    """Would fail if cached pandemic-era zero attendance were normalized as missing."""
    result = bref.load_attendance(RAW / "spike", 2021, ["december"])

    assert len(result) == 67
    assert result["attendance"].eq(0).sum() == 56
    assert result["attendance"].gt(0).sum() == 11
