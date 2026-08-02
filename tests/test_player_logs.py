"""Tests for exact NBA player game-log normalization."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import requests

from nba_data import cache, player_logs


HEADERS = [
    "SEASON_ID", "PLAYER_ID", "PLAYER_NAME", "TEAM_ID",
    "TEAM_ABBREVIATION", "TEAM_NAME", "GAME_ID", "GAME_DATE",
    "MATCHUP", "WL", "MIN", "FGM", "FGA", "FG_PCT", "FG3M",
    "FG3A", "FG3_PCT", "FTM", "FTA", "FT_PCT", "OREB", "DREB",
    "REB", "AST", "STL", "BLK", "TOV", "PF", "PTS", "PLUS_MINUS",
    "FANTASY_PTS", "VIDEO_AVAILABLE",
]


@pytest.fixture(autouse=True)
def _keep_fetch_tests_offline(request, monkeypatch):
    if not request.node.name.startswith("test_fetch"):
        return

    def successful_response(*args, **kwargs):
        response = requests.Response()
        response.status_code = 200
        response.url = "https://stats.nba.com/stats/leaguegamelog"
        response._content = b"{}"
        return response

    monkeypatch.setattr(player_logs.requests, "get", successful_response)


def _row(
    player_id: int,
    player_name: str,
    team_id: int,
    abbreviation: str,
    team_name: str,
    matchup: str,
    minutes: int,
    points: int,
) -> list[object]:
    return [
        "21983", player_id, player_name, team_id, abbreviation, team_name,
        "0028300001", "1983-10-28", matchup, "W" if abbreviation == "BOS" else "L",
        minutes, 1 if points else 0, 2 if points else 0, 0.5 if points else 0.0,
        0, 0, 0.0, 0, 0, 0.0, 0, 1 if minutes else 0,
        1 if minutes else 0, 1 if minutes else 0, 0, 0, 0, 1 if minutes else 0,
        points, None, 0.0, 0,
    ]


REGULAR_ROWS = [
    _row(1, "Chicago One", 1610612741, "CHI", "Chicago Bulls", "CHI @ BOS", 48, 2),
    _row(2, "Chicago Zero", 1610612741, "CHI", "Chicago Bulls", "CHI @ BOS", 0, 0),
    _row(3, "Boston One", 1610612738, "BOS", "Boston Celtics", "BOS vs. CHI", 48, 2),
    _row(4, "Boston Two", 1610612738, "BOS", "Boston Celtics", "BOS vs. CHI", 24, 2),
]


def _payload(rows: list[list[object]], headers=HEADERS) -> bytes:
    return json.dumps({
        "resultSets": [{
            "name": "LeagueGameLog",
            "headers": list(headers),
            "rowSet": rows,
        }]
    }).encode("utf-8")


def test_normalize_payload_preserves_source_and_nullable_semantics():
    frame = player_logs.normalize_payload(
        _payload(REGULAR_ROWS), season=1984, game_type="regular"
    )
    assert frame.columns.tolist() == player_logs.PLAYER_GAME_COLUMNS
    assert frame["game_id"].unique().tolist() == ["0028300001"]
    assert frame["person_id"].dtype == "int64"
    assert frame.loc[frame.person_id.eq(2), "min"].item() == 0
    assert frame["plus_minus"].isna().all()
    assert set(frame.loc[frame.is_home, "franchise_id"]) == {"BOS"}
    assert set(frame.loc[~frame.is_home, "franchise_id"]) == {"CHI"}
    assert set(frame.loc[frame.franchise_id.eq("BOS"), "opponent_franchise_id"]) == {"CHI"}


def _document(rows=REGULAR_ROWS, headers=HEADERS, *, result_sets=None):
    return {
        "resultSets": result_sets if result_sets is not None else [{
            "name": "LeagueGameLog",
            "headers": list(headers),
            "rowSet": [list(row) for row in rows],
        }]
    }


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (b"{", "invalid player-log JSON"),
        (json.dumps([]).encode(), "must be a JSON object"),
        (json.dumps({}).encode(), "missing resultSets"),
        (_payload(REGULAR_ROWS, HEADERS + ["MIN"]), "duplicate header"),
        (_payload(REGULAR_ROWS, [header for header in HEADERS if header != "PTS"]), "missing required header"),
        (json.dumps(_document(result_sets=[{
            "name": "Other", "headers": HEADERS, "rowSet": REGULAR_ROWS,
        }])).encode(), "expected exactly one 'LeagueGameLog' result set"),
        (json.dumps(_document(result_sets=[
            {"name": "LeagueGameLog", "headers": HEADERS, "rowSet": REGULAR_ROWS},
            {"name": "LeagueGameLog", "headers": HEADERS, "rowSet": REGULAR_ROWS},
        ])).encode(), "expected exactly one 'LeagueGameLog' result set"),
        (json.dumps(_document(result_sets=[{
            "name": "LeagueGameLog", "headers": HEADERS, "rowSet": {},
        }])).encode(), "rowSet must be a list"),
        (_payload([REGULAR_ROWS[0][:-1]]), "has width 31, expected row width 32"),
    ],
)
def test_validate_payload_bytes_rejects_invalid_source_envelopes(payload, match):
    with pytest.raises(ValueError, match=match):
        player_logs.validate_payload_bytes(payload)


def test_validate_payload_bytes_rejects_an_extra_wrong_result_set():
    payload = json.dumps(_document(result_sets=[
        {"name": "Other", "headers": HEADERS, "rowSet": REGULAR_ROWS},
        {"name": "LeagueGameLog", "headers": HEADERS, "rowSet": REGULAR_ROWS},
    ])).encode()
    with pytest.raises(ValueError, match="expected exactly one 'LeagueGameLog' result set"):
        player_logs.validate_payload_bytes(payload)


def test_source_helpers_define_season_scope_cache_location_and_file_validation(tmp_path):
    assert player_logs.season_label(1984) == "1983-84"
    assert player_logs.applicable_game_types(1984) == ("regular", "playoff")
    assert player_logs.applicable_game_types(2020) == (
        "regular", "playoff", "play_in"
    )
    assert player_logs.cache_path(tmp_path, 1984, "regular") == (
        tmp_path / "raw/stats_nba/player_logs/season=1984/regular.json"
    )
    payload_path = tmp_path / "source.json"
    payload_path.write_bytes(_payload(REGULAR_ROWS))
    assert player_logs.validate_payload(payload_path) is None


def test_read_cached_accepts_only_the_exact_sidecar_identity(tmp_path):
    """Would fail if a cache pair from a different LeagueGameLog request passed."""
    destination = player_logs.cache_path(tmp_path, 1984, "regular")
    expected = {
        "provider": "stats_nba",
        "endpoint": "LeagueGameLog",
        "season": 1984,
        "season_label": "1983-84",
        "game_type": "regular",
        "season_type": "Regular Season",
        "league_id": "00",
        "player_or_team_abbreviation": "P",
    }
    payload = _payload(REGULAR_ROWS)
    cache.publish_bytes(destination, payload, expected, player_logs.validate_payload)

    cached_payload, metadata = player_logs.read_cached(tmp_path, 1984, "regular")

    assert cached_payload == payload
    assert {key: metadata[key] for key in expected} == expected
    assert metadata["content_length"] == len(payload)


def _cache_metadata(season=1984, game_type="regular"):
    return {
        "provider": "stats_nba",
        "endpoint": "LeagueGameLog",
        "season": season,
        "season_label": f"{season - 1}-{season % 100:02d}",
        "game_type": game_type,
        "season_type": {
            "regular": "Regular Season", "playoff": "Playoffs", "play_in": "PlayIn",
        }[game_type],
        "league_id": "00",
        "player_or_team_abbreviation": "P",
    }


@pytest.mark.parametrize("missing", ["body", "sidecar"])
def test_read_cached_rejects_incomplete_pairs(tmp_path, missing):
    """Would fail if a partial immutable cache pair were treated as a hit."""
    destination = player_logs.cache_path(tmp_path, 1984, "regular")
    cache.publish_bytes(
        destination, _payload(REGULAR_ROWS), _cache_metadata(), player_logs.validate_payload
    )
    (destination if missing == "body" else destination.with_name(
        destination.name + ".meta.json"
    )).unlink()

    with pytest.raises(cache.CacheIntegrityError, match="incomplete cache pair"):
        player_logs.read_cached(tmp_path, 1984, "regular")


@pytest.mark.parametrize("field", tuple(_cache_metadata()))
def test_read_cached_rejects_each_wrong_identity_field(tmp_path, field):
    """Would fail if a response for a different endpoint identity passed."""
    destination = player_logs.cache_path(tmp_path, 1984, "regular")
    cache.publish_bytes(
        destination, _payload(REGULAR_ROWS), _cache_metadata(), player_logs.validate_payload
    )
    sidecar = destination.with_name(destination.name + ".meta.json")
    metadata = json.loads(sidecar.read_text())
    metadata[field] = "wrong"
    sidecar.write_text(json.dumps(metadata))

    with pytest.raises(cache.CacheIntegrityError, match="metadata mismatch"):
        player_logs.read_cached(tmp_path, 1984, "regular")


@pytest.mark.parametrize("field", tuple(_cache_metadata()))
def test_read_cached_rejects_missing_identity_field(tmp_path, field):
    """Would fail if a sidecar could omit one identity constraint."""
    destination = player_logs.cache_path(tmp_path, 1984, "regular")
    cache.publish_bytes(
        destination, _payload(REGULAR_ROWS), _cache_metadata(), player_logs.validate_payload
    )
    sidecar = destination.with_name(destination.name + ".meta.json")
    metadata = json.loads(sidecar.read_text())
    del metadata[field]
    sidecar.write_text(json.dumps(metadata))

    with pytest.raises(cache.CacheIntegrityError, match="metadata mismatch"):
        player_logs.read_cached(tmp_path, 1984, "regular")


def test_read_cached_rejects_content_length_mismatch(tmp_path):
    """Would fail if an attested body length was not bound to its sidecar."""
    destination = player_logs.cache_path(tmp_path, 1984, "regular")
    cache.publish_bytes(
        destination, _payload(REGULAR_ROWS), _cache_metadata(), player_logs.validate_payload
    )
    sidecar = destination.with_name(destination.name + ".meta.json")
    metadata = json.loads(sidecar.read_text())
    metadata["content_length"] += 1
    sidecar.write_text(json.dumps(metadata))

    with pytest.raises(cache.CacheIntegrityError, match="content length mismatch"):
        player_logs.read_cached(tmp_path, 1984, "regular")


def test_read_cached_rejects_a_stable_body_sidecar_sha_mismatch(tmp_path):
    """Would fail if a persisted body mutation did not invalidate its sidecar."""
    destination = player_logs.cache_path(tmp_path, 1984, "regular")
    cache.publish_bytes(
        destination, _payload(REGULAR_ROWS), _cache_metadata(), player_logs.validate_payload
    )
    destination.write_bytes(_payload(REGULAR_ROWS) + b" ")

    with pytest.raises(cache.CacheIntegrityError, match="SHA-256 mismatch"):
        player_logs.read_cached(tmp_path, 1984, "regular")


@pytest.mark.parametrize("unsafe_kind", ["symlink", "fifo"])
def test_read_cached_rejects_symlink_and_special_body_entries(tmp_path, unsafe_kind):
    """Would fail if an unsafe filesystem entry were opened as cache data."""
    destination = player_logs.cache_path(tmp_path, 1984, "regular")
    destination.parent.mkdir(parents=True)
    sidecar = destination.with_name(destination.name + ".meta.json")
    sidecar.write_text(json.dumps({"sha256": "unused"}))
    if unsafe_kind == "symlink":
        target = tmp_path / "target.json"
        target.write_bytes(_payload(REGULAR_ROWS))
        destination.symlink_to(target)
    else:
        os.mkfifo(destination)

    with pytest.raises(cache.CacheIntegrityError, match="regular files"):
        player_logs.read_cached(tmp_path, 1984, "regular")


@pytest.mark.parametrize("mutated", ["body", "sidecar"])
def test_read_cached_rejects_pair_mutated_during_attestation(
    tmp_path, monkeypatch, mutated
):
    """Would fail if read_cached returned a snapshot changed while it was read."""
    destination = player_logs.cache_path(tmp_path, 1984, "regular")
    cache.publish_bytes(
        destination, _payload(REGULAR_ROWS), _cache_metadata(), player_logs.validate_payload
    )
    target = (
        destination if mutated == "body"
        else destination.with_name(destination.name + ".meta.json")
    )
    original_read_bytes = Path.read_bytes
    did_mutate = False

    def mutate_after_read(path):
        nonlocal did_mutate
        value = original_read_bytes(path)
        if path == target and not did_mutate:
            did_mutate = True
            path.write_bytes(b"changed")
        return value

    monkeypatch.setattr(Path, "read_bytes", mutate_after_read)

    with pytest.raises(cache.CacheIntegrityError, match="changed while"):
        player_logs.read_cached(tmp_path, 1984, "regular")


def _seed_full_player_log_cache(data_dir):
    bodies = []
    for season in player_logs.SEASONS:
        for game_type in player_logs.applicable_game_types(season):
            destination = player_logs.cache_path(data_dir, season, game_type)
            cache.publish_bytes(
                destination,
                _payload(REGULAR_ROWS),
                _cache_metadata(season, game_type),
                player_logs.validate_payload,
            )
            bodies.append(destination)
    return data_dir / "raw/stats_nba/player_logs", tuple(bodies)


def test_validate_cache_inventory_root_requires_all_exact_cache_pairs(tmp_path):
    """Would fail if one missing or renamed cache entry passed the inventory."""
    root, bodies = _seed_full_player_log_cache(tmp_path)

    inventory = player_logs.validate_cache_inventory_root(root)

    assert len(inventory) == 93
    assert inventory == bodies
    assert inventory[:3] == (
        root / "season=1984/regular.json",
        root / "season=1984/playoff.json",
        root / "season=1985/regular.json",
    )


def test_validate_cache_inventory_uses_the_player_log_raw_root(tmp_path):
    """Would fail if the data-directory wrapper inspected a sibling cache root."""
    _, bodies = _seed_full_player_log_cache(tmp_path)

    assert player_logs.validate_cache_inventory(tmp_path) == bodies


@pytest.mark.parametrize("unsafe_kind", ["symlink", "fifo"])
def test_validate_cache_inventory_root_rejects_unsafe_entries(
    tmp_path, unsafe_kind
):
    """Would fail if a symlink or special filesystem entry escaped validation."""
    root, _ = _seed_full_player_log_cache(tmp_path)
    entry = root / "season=1984" / "unsafe"
    if unsafe_kind == "symlink":
        entry.symlink_to(root / "season=1984/regular.json")
    else:
        os.mkfifo(entry)

    with pytest.raises(cache.CacheIntegrityError, match="unsafe|unexpected"):
        player_logs.validate_cache_inventory_root(root)


def test_validate_cache_inventory_root_rejects_an_unexpected_file(tmp_path):
    """Would fail if an unrecognized cache file were silently ignored."""
    root, _ = _seed_full_player_log_cache(tmp_path)
    (root / "season=1984" / "extra.json").write_bytes(b"unexpected")

    with pytest.raises(cache.CacheIntegrityError, match="unexpected"):
        player_logs.validate_cache_inventory_root(root)


@pytest.mark.parametrize("missing", ["body", "sidecar"])
def test_validate_cache_inventory_root_rejects_missing_expected_pair_member(
    tmp_path, missing
):
    """Would fail if a missing exact inventory member were treated as complete."""
    root, _ = _seed_full_player_log_cache(tmp_path)
    destination = root / "season=1984" / "regular.json"
    target = destination if missing == "body" else destination.with_name(
        destination.name + ".meta.json"
    )
    target.unlink()

    with pytest.raises(cache.CacheIntegrityError, match="missing"):
        player_logs.validate_cache_inventory_root(root)


@pytest.mark.parametrize("renamed", ["body", "sidecar"])
def test_validate_cache_inventory_root_rejects_renamed_expected_pair_member(
    tmp_path, renamed
):
    """Would fail if a renamed exact inventory member were treated as complete."""
    root, _ = _seed_full_player_log_cache(tmp_path)
    destination = root / "season=1984" / "regular.json"
    target = destination if renamed == "body" else destination.with_name(
        destination.name + ".meta.json"
    )
    target.rename(target.with_name("renamed-" + target.name))

    with pytest.raises(cache.CacheIntegrityError, match="unexpected"):
        player_logs.validate_cache_inventory_root(root)


def test_fetch_retries_four_times_then_publishes(monkeypatch, tmp_path):
    """Would fail if transient endpoint failures did not use five total attempts."""
    attempts = []
    sleeps = []
    published = []

    class FakeLeagueGameLog:
        def __init__(self, **kwargs):
            attempts.append(kwargs)
            if len(attempts) < 5:
                raise requests.Timeout("transient")

        def get_json(self):
            return _payload(REGULAR_ROWS).decode("utf-8")

    class FakeTime:
        now = 0.0

        @classmethod
        def monotonic(cls):
            return cls.now

        @classmethod
        def sleep(cls, delay):
            sleeps.append(delay)
            cls.now += delay

    fake_time = FakeTime
    monkeypatch.setattr(player_logs, "LeagueGameLog", FakeLeagueGameLog, raising=False)
    monkeypatch.setattr(
        player_logs, "applicable_game_types", lambda season: ("regular",)
    )
    monkeypatch.setattr(
        player_logs,
        "available_bytes",
        lambda path: player_logs.FETCH_HEADROOM_BYTES,
        raising=False,
    )
    monkeypatch.setattr(player_logs, "read_cached", lambda *args: None)
    monkeypatch.setattr(
        player_logs,
        "stats_nba",
        type("StatsNBA", (), {"preflight_dns": staticmethod(lambda: ("203.0.113.1",))}),
        raising=False,
    )
    monkeypatch.setattr(player_logs, "time", fake_time, raising=False)
    monkeypatch.setattr(
        player_logs.cache,
        "publish_bytes",
        lambda *args: published.append(args),
    )

    player_logs.fetch(tmp_path, [1984])

    assert sleeps == [5.0, 10.0, 20.0, 40.0]
    assert len(attempts) == 5
    assert {key: attempts[-1][key] for key in (
        "league_id", "player_or_team_abbreviation", "season",
        "season_type_all_star", "direction", "sorter", "timeout",
    )} == {
        "league_id": "00",
        "player_or_team_abbreviation": "P",
        "season": "1983-84",
        "season_type_all_star": "Regular Season",
        "direction": "ASC",
        "sorter": "DATE",
        "timeout": 90,
    }
    assert attempts[-1]["get_request"] is False
    assert len(published) == 1


def test_fetch_retries_real_http_429_before_endpoint_parsing(monkeypatch, tmp_path):
    """Would fail if nba_api parsed a 429 body before fetch handled its status."""
    attempts = []
    sleeps = []
    responses = []
    for status, retry_after in ((429, "15"), (200, None)):
        response = requests.Response()
        response.status_code = status
        response.url = "https://stats.nba.com/stats/leaguegamelog"
        response._content = b'{}'
        if retry_after is not None:
            response.headers["Retry-After"] = retry_after
        responses.append(response)

    class FakeTime:
        now = 0.0

        @classmethod
        def monotonic(cls):
            return cls.now

        @classmethod
        def sleep(cls, delay):
            sleeps.append(delay)
            cls.now += delay

    class FakeLeagueGameLog:
        def __init__(self, **kwargs):
            attempts.append(kwargs)
            if kwargs.get("get_request", True):
                raise KeyError("resultSet")

        def get_json(self):
            return _payload(REGULAR_ROWS).decode()

    monkeypatch.setattr(player_logs, "LeagueGameLog", FakeLeagueGameLog)
    monkeypatch.setattr(
        player_logs.requests, "get", lambda *args, **kwargs: responses.pop(0)
    )
    monkeypatch.setattr(player_logs, "time", FakeTime)
    monkeypatch.setattr(player_logs, "applicable_game_types", lambda _: ("regular",))
    monkeypatch.setattr(player_logs, "available_bytes", lambda _: player_logs.FETCH_HEADROOM_BYTES)
    monkeypatch.setattr(player_logs.stats_nba, "preflight_dns", lambda: ("203.0.113.1",))

    player_logs.fetch(tmp_path, [1984])

    assert len(attempts) == 2
    assert all(item["get_request"] is False for item in attempts)
    assert sleeps == [15.0]


def test_fetch_skips_only_complete_valid_pairs(monkeypatch, tmp_path):
    """Would fail if a valid pair was fetched again or a miss was skipped."""
    complete = player_logs.cache_path(tmp_path, 1984, "regular")
    cache.publish_bytes(
        complete, _payload(REGULAR_ROWS), _cache_metadata(), player_logs.validate_payload
    )
    requests_seen = []

    class FakeLeagueGameLog:
        def __init__(self, **kwargs):
            requests_seen.append(kwargs)

        def get_json(self):
            return _payload(REGULAR_ROWS).decode()

    monkeypatch.setattr(player_logs, "LeagueGameLog", FakeLeagueGameLog)
    monkeypatch.setattr(player_logs, "applicable_game_types", lambda _: ("regular", "playoff"))
    monkeypatch.setattr(player_logs, "available_bytes", lambda _: player_logs.FETCH_HEADROOM_BYTES)
    monkeypatch.setattr(player_logs.stats_nba, "preflight_dns", lambda: ("203.0.113.1",))

    player_logs.fetch(tmp_path, [1984])

    assert [item["season_type_all_star"] for item in requests_seen] == ["Playoffs"]


def test_fetch_requests_exact_endpoint_identity(monkeypatch, tmp_path):
    """Would fail if fetching selected an aggregate or team LeagueGameLog view."""
    requests_seen = []

    class FakeLeagueGameLog:
        def __init__(self, **kwargs):
            requests_seen.append(kwargs)

        def get_json(self):
            return _payload(REGULAR_ROWS).decode()

    monkeypatch.setattr(player_logs, "LeagueGameLog", FakeLeagueGameLog)
    monkeypatch.setattr(player_logs, "applicable_game_types", lambda _: ("regular",))
    monkeypatch.setattr(player_logs, "available_bytes", lambda _: player_logs.FETCH_HEADROOM_BYTES)
    monkeypatch.setattr(player_logs.stats_nba, "preflight_dns", lambda: ("203.0.113.1",))

    player_logs.fetch(tmp_path, [1984])

    assert [{key: item[key] for key in (
        "league_id", "player_or_team_abbreviation", "season",
        "season_type_all_star", "direction", "sorter", "timeout",
    )} for item in requests_seen] == [{
        "league_id": "00", "player_or_team_abbreviation": "P",
        "season": "1983-84", "season_type_all_star": "Regular Season",
        "direction": "ASC", "sorter": "DATE", "timeout": 90,
    }]
    assert [item["get_request"] for item in requests_seen] == [False]


def test_fetch_spaces_request_starts_at_least_1_5_seconds(monkeypatch, tmp_path):
    """Would fail if two serialized requests could start less than 1.5 seconds apart."""
    starts = []
    sleeps = []

    class FakeTime:
        now = 10.0

        @classmethod
        def monotonic(cls):
            return cls.now

        @classmethod
        def sleep(cls, delay):
            sleeps.append(delay)
            cls.now += delay

    class FakeLeagueGameLog:
        def __init__(self, **kwargs):
            pass

        def get_json(self):
            return _payload(REGULAR_ROWS).decode()

    def raw_response(*args, **kwargs):
        starts.append(FakeTime.monotonic())
        response = requests.Response()
        response.status_code = 200
        response.url = "https://stats.nba.com/stats/leaguegamelog"
        response._content = b"{}"
        return response

    monkeypatch.setattr(player_logs, "LeagueGameLog", FakeLeagueGameLog)
    monkeypatch.setattr(player_logs.requests, "get", raw_response)
    monkeypatch.setattr(player_logs, "time", FakeTime)
    monkeypatch.setattr(player_logs, "applicable_game_types", lambda _: ("regular", "playoff"))
    monkeypatch.setattr(player_logs, "available_bytes", lambda _: player_logs.FETCH_HEADROOM_BYTES)
    monkeypatch.setattr(player_logs.stats_nba, "preflight_dns", lambda: ("203.0.113.1",))

    player_logs.fetch(tmp_path, [1984])

    assert starts == [10.0, 11.5]
    assert sleeps == [1.5]


def test_fetch_spaces_raw_http_request_starts_at_least_1_5_seconds(
    monkeypatch, tmp_path
):
    """Would fail if constructor time were mistaken for the HTTP request start."""
    starts = []

    class FakeTime:
        now = 10.0

        @classmethod
        def monotonic(cls):
            return cls.now

        @classmethod
        def sleep(cls, delay):
            cls.now += delay

    class FakeLeagueGameLog:
        constructed = 0

        def __init__(self, **kwargs):
            type(self).constructed += 1
            if type(self).constructed == 1:
                FakeTime.now += 1.4

        def get_json(self):
            return _payload(REGULAR_ROWS).decode()

    def raw_response(*args, **kwargs):
        starts.append(FakeTime.monotonic())
        response = requests.Response()
        response.status_code = 200
        response.url = "https://stats.nba.com/stats/leaguegamelog"
        response._content = b"{}"
        return response

    monkeypatch.setattr(player_logs, "LeagueGameLog", FakeLeagueGameLog)
    monkeypatch.setattr(player_logs, "requests", type(
        "Requests", (), {"get": staticmethod(raw_response), "RequestException": requests.RequestException,
                            "HTTPError": requests.HTTPError}
    ))
    monkeypatch.setattr(player_logs, "time", FakeTime)
    monkeypatch.setattr(player_logs, "applicable_game_types", lambda _: ("regular", "playoff"))
    monkeypatch.setattr(player_logs, "available_bytes", lambda _: player_logs.FETCH_HEADROOM_BYTES)
    monkeypatch.setattr(player_logs.stats_nba, "preflight_dns", lambda: ("203.0.113.1",))

    player_logs.fetch(tmp_path, [1984])

    assert starts[1] - starts[0] >= 1.5


def test_fetch_honors_numeric_retry_after_through_120(monkeypatch, tmp_path):
    """Would fail if a permitted server retry delay were ignored or capped early."""
    attempts = []
    sleeps = []

    class FakeTime:
        now = 0.0

        @classmethod
        def monotonic(cls):
            return cls.now

        @classmethod
        def sleep(cls, delay):
            sleeps.append(delay)
            cls.now += delay

    class FakeLeagueGameLog:
        def __init__(self, **kwargs):
            attempts.append(kwargs)
            if kwargs.get("get_request", True):
                raise KeyError("resultSet")

        def get_json(self):
            return _payload(REGULAR_ROWS).decode()

    monkeypatch.setattr(player_logs, "LeagueGameLog", FakeLeagueGameLog)
    responses = []
    for status, retry_after in ((429, "120"), (200, None)):
        response = requests.Response()
        response.status_code = status
        response.url = "https://stats.nba.com/stats/leaguegamelog"
        response._content = b"{}"
        if retry_after is not None:
            response.headers["Retry-After"] = retry_after
        responses.append(response)
    monkeypatch.setattr(
        player_logs.requests, "get", lambda *args, **kwargs: responses.pop(0)
    )
    monkeypatch.setattr(player_logs, "time", FakeTime)
    monkeypatch.setattr(player_logs, "applicable_game_types", lambda _: ("regular",))
    monkeypatch.setattr(player_logs, "available_bytes", lambda _: player_logs.FETCH_HEADROOM_BYTES)
    monkeypatch.setattr(player_logs.stats_nba, "preflight_dns", lambda: ("203.0.113.1",))

    player_logs.fetch(tmp_path, [1984])

    assert len(attempts) == 2
    assert sleeps == [120.0]


def test_fetch_ignores_nonnumeric_retry_after_and_uses_fixed_backoff(
    monkeypatch, tmp_path
):
    """Would fail if a malformed transient Retry-After stopped acquisition."""
    sleeps = []
    responses = []
    for status, retry_after in ((503, "soon"), (200, None)):
        response = requests.Response()
        response.status_code = status
        response.url = "https://stats.nba.com/stats/leaguegamelog"
        response._content = b"{}"
        if retry_after is not None:
            response.headers["Retry-After"] = retry_after
        responses.append(response)

    class FakeTime:
        now = 0.0

        @classmethod
        def monotonic(cls):
            return cls.now

        @classmethod
        def sleep(cls, delay):
            sleeps.append(delay)
            cls.now += delay

    class FakeLeagueGameLog:
        def __init__(self, **kwargs):
            assert kwargs["get_request"] is False

        def get_json(self):
            return _payload(REGULAR_ROWS).decode()

    monkeypatch.setattr(player_logs, "LeagueGameLog", FakeLeagueGameLog)
    monkeypatch.setattr(
        player_logs.requests, "get", lambda *args, **kwargs: responses.pop(0)
    )
    monkeypatch.setattr(player_logs, "time", FakeTime)
    monkeypatch.setattr(player_logs, "applicable_game_types", lambda _: ("regular",))
    monkeypatch.setattr(player_logs, "available_bytes", lambda _: player_logs.FETCH_HEADROOM_BYTES)
    monkeypatch.setattr(player_logs.stats_nba, "preflight_dns", lambda: ("203.0.113.1",))

    player_logs.fetch(tmp_path, [1984])

    assert sleeps == [5.0]


def test_fetch_stops_on_retry_after_over_120(monkeypatch, tmp_path):
    """Would fail if an excessive server retry delay still issued another request."""
    attempts = []

    class FakeLeagueGameLog:
        def __init__(self, **kwargs):
            attempts.append(kwargs)
            if kwargs.get("get_request", True):
                raise KeyError("resultSet")

    monkeypatch.setattr(player_logs, "LeagueGameLog", FakeLeagueGameLog)
    response = requests.Response()
    response.status_code = 429
    response.url = "https://stats.nba.com/stats/leaguegamelog"
    response._content = b"{}"
    response.headers["Retry-After"] = "121"
    monkeypatch.setattr(player_logs.requests, "get", lambda *args, **kwargs: response)
    monkeypatch.setattr(player_logs, "applicable_game_types", lambda _: ("regular",))
    monkeypatch.setattr(player_logs, "available_bytes", lambda _: player_logs.FETCH_HEADROOM_BYTES)
    monkeypatch.setattr(player_logs.stats_nba, "preflight_dns", lambda: ("203.0.113.1",))

    with pytest.raises(cache.SourceAccessError, match="exceeds 120"):
        player_logs.fetch(tmp_path, [1984])

    assert len(attempts) == 1


def test_fetch_stops_on_non_429_4xx(monkeypatch, tmp_path):
    """Would fail if a permanent client error were retried as a transient failure."""
    attempts = []

    class FakeLeagueGameLog:
        def __init__(self, **kwargs):
            attempts.append(kwargs)
            if kwargs.get("get_request", True):
                raise KeyError("resultSet")

    monkeypatch.setattr(player_logs, "LeagueGameLog", FakeLeagueGameLog)
    response = requests.Response()
    response.status_code = 404
    response.url = "https://stats.nba.com/stats/leaguegamelog"
    response._content = b"{}"
    monkeypatch.setattr(player_logs.requests, "get", lambda *args, **kwargs: response)
    monkeypatch.setattr(player_logs, "applicable_game_types", lambda _: ("regular",))
    monkeypatch.setattr(player_logs, "available_bytes", lambda _: player_logs.FETCH_HEADROOM_BYTES)
    monkeypatch.setattr(player_logs.stats_nba, "preflight_dns", lambda: ("203.0.113.1",))

    with pytest.raises(cache.SourceAccessError, match="HTTP 404"):
        player_logs.fetch(tmp_path, [1984])

    assert len(attempts) == 1


def test_fetch_does_not_replace_invalid_existing_pair(monkeypatch, tmp_path):
    """Would fail if fetch repaired or replaced an incomplete immutable cache pair."""
    destination = player_logs.cache_path(tmp_path, 1984, "regular")
    destination.parent.mkdir(parents=True)
    destination.write_bytes(_payload(REGULAR_ROWS))

    monkeypatch.setattr(
        player_logs, "LeagueGameLog", lambda **kwargs: pytest.fail("endpoint called")
    )
    monkeypatch.setattr(player_logs, "applicable_game_types", lambda _: ("regular",))

    with pytest.raises(cache.CacheIntegrityError, match="incomplete cache pair"):
        player_logs.fetch(tmp_path, [1984])


def test_fetch_validates_every_selected_cache_pair_before_any_acquisition(
    monkeypatch, tmp_path
):
    """Would fail if an early miss fetched before a later invalid selected pair."""
    invalid = player_logs.cache_path(tmp_path, 1984, "playoff")
    invalid.parent.mkdir(parents=True)
    invalid.write_bytes(_payload(REGULAR_ROWS))
    constructors = []
    published = []

    class FakeLeagueGameLog:
        def __init__(self, **kwargs):
            constructors.append(kwargs)

        def get_json(self):
            return _payload(REGULAR_ROWS).decode()

    monkeypatch.setattr(player_logs, "LeagueGameLog", FakeLeagueGameLog)
    monkeypatch.setattr(
        player_logs, "applicable_game_types", lambda _: ("regular", "playoff")
    )
    monkeypatch.setattr(player_logs, "available_bytes", lambda _: player_logs.FETCH_HEADROOM_BYTES)
    monkeypatch.setattr(player_logs.cache, "publish_bytes", lambda *args: published.append(args))

    with pytest.raises(cache.CacheIntegrityError, match="incomplete cache pair"):
        player_logs.fetch(tmp_path, [1984])

    assert constructors == []
    assert published == []


def test_fetch_requires_five_gibibytes_free_before_first_uncached_request(
    monkeypatch, tmp_path
):
    """Would fail if fetch started a request without its fixed staging headroom."""
    capacity_paths = []
    monkeypatch.setattr(player_logs, "applicable_game_types", lambda _: ("regular",))
    monkeypatch.setattr(
        player_logs,
        "available_bytes",
        lambda path: capacity_paths.append(path) or player_logs.FETCH_HEADROOM_BYTES - 1,
    )
    monkeypatch.setattr(
        player_logs, "LeagueGameLog", lambda **kwargs: pytest.fail("endpoint called")
    )
    monkeypatch.setattr(
        player_logs.stats_nba, "preflight_dns", lambda: pytest.fail("DNS called")
    )

    with pytest.raises(ValueError, match="insufficient fetch capacity"):
        player_logs.fetch(tmp_path, [1984])

    assert capacity_paths == [tmp_path]


@pytest.mark.parametrize("season", [1983, 2027])
def test_source_helpers_reject_seasons_outside_the_publication_range(season):
    with pytest.raises(ValueError, match="1984..2026"):
        player_logs.season_label(season)


def _rows_with(field: str, value: object) -> list[list[object]]:
    rows = [list(row) for row in REGULAR_ROWS]
    rows[0][HEADERS.index(field)] = value
    return rows


@pytest.mark.parametrize(
    ("payload", "season", "game_type", "match"),
    [
        (_payload(REGULAR_ROWS), 1984, "exhibition", "unknown game type"),
        (_payload(REGULAR_ROWS), 1983, "regular", "1984..2026"),
        (_payload(_rows_with("SEASON_ID", "21982")), 1984, "regular", "ending season mismatch"),
        (_payload(_rows_with("GAME_ID", "0048300001")), 1984, "regular", "GAME_ID prefix mismatch"),
        (_payload(_rows_with("MATCHUP", "CHI - BOS")), 1984, "regular", "MATCHUP does not match"),
        (_payload(_rows_with("TEAM_ABBREVIATION", "NYK")), 1984, "regular", "exactly one side"),
        (_payload(_rows_with("MATCHUP", "CHI @ CHI")), 1984, "regular", "exactly one side"),
        (_payload(_rows_with("PLAYER_ID", 1.5)), 1984, "regular", "PLAYER_ID must be an exact integer"),
        (_payload(_rows_with("PTS", 1.5)), 1984, "regular", "PTS must be an exact integer"),
        (_payload(_rows_with("PTS", -1)), 1984, "regular", "PTS must be non-negative"),
        (_payload(_rows_with("PLAYER_NAME", None)), 1984, "regular", "PLAYER_NAME must not be null"),
    ],
)
def test_normalize_payload_rejects_invalid_source_values(
    payload, season, game_type, match
):
    with pytest.raises(ValueError, match=match):
        player_logs.normalize_payload(payload, season=season, game_type=game_type)


def test_normalize_payload_rejects_games_with_one_or_three_team_identities():
    one_team_rows = [list(row) for row in REGULAR_ROWS]
    for row in one_team_rows:
        if row[HEADERS.index("TEAM_ABBREVIATION")] == "BOS":
            row[HEADERS.index("TEAM_ID")] = 1610612741
            row[HEADERS.index("TEAM_NAME")] = "Chicago Bulls"
            row[HEADERS.index("TEAM_ABBREVIATION")] = "CHI"
            row[HEADERS.index("MATCHUP")] = "CHI @ BOS"
    three_team_rows = [list(row) for row in REGULAR_ROWS]
    third = list(three_team_rows[0])
    third[HEADERS.index("PLAYER_ID")] = 5
    third[HEADERS.index("TEAM_ID")] = 1610612752
    third[HEADERS.index("TEAM_ABBREVIATION")] = "NYK"
    third[HEADERS.index("TEAM_NAME")] = "New York Knicks"
    third[HEADERS.index("MATCHUP")] = "NYK @ BOS"
    three_team_rows.append(third)

    for rows in (one_team_rows, three_team_rows):
        with pytest.raises(ValueError, match="team identities, expected 2"):
            player_logs.normalize_payload(_payload(rows), season=1984, game_type="regular")


def test_normalize_payload_rejects_two_source_teams_resolving_to_one_franchise():
    rows = [list(row) for row in REGULAR_ROWS]
    for row in rows:
        if row[HEADERS.index("TEAM_ABBREVIATION")] == "CHI":
            row[HEADERS.index("TEAM_NAME")] = "Boston Celtics"
    with pytest.raises(ValueError, match="same resolved franchise"):
        player_logs.normalize_payload(_payload(rows), season=1984, game_type="regular")


def test_normalize_payload_rejects_duplicate_game_and_person_key():
    rows = [list(row) for row in REGULAR_ROWS]
    rows.append(list(rows[0]))
    with pytest.raises(ValueError, match=r"duplicate \(game_id, person_id\)"):
        player_logs.normalize_payload(_payload(rows), season=1984, game_type="regular")


def test_normalize_payload_assigns_neutral_style_rows_by_each_players_position():
    rows = [list(row) for row in REGULAR_ROWS]
    for row in rows:
        row[HEADERS.index("MATCHUP")] = "CHI @ BOS"

    frame = player_logs.normalize_payload(_payload(rows), season=1984, game_type="regular")
    assert set(frame.loc[frame.is_home, "franchise_id"]) == {"BOS"}
    assert set(frame.loc[~frame.is_home, "franchise_id"]) == {"CHI"}


def test_normalize_payload_rejects_identifier_outside_signed_int64_range():
    rows = _rows_with("PLAYER_ID", 1e20)
    with pytest.raises(ValueError, match="PLAYER_ID must be an exact integer"):
        player_logs.normalize_payload(_payload(rows), season=1984, game_type="regular")


def test_normalize_payload_rejects_non_standard_json_numeric_constants():
    rows = _rows_with("PTS", float("nan"))
    with pytest.raises(ValueError, match="invalid player-log JSON"):
        player_logs.normalize_payload(_payload(rows), season=1984, game_type="regular")


def test_normalize_payload_rejects_team_rows_that_disagree_on_matchup_pair():
    rows = [list(row) for row in REGULAR_ROWS]
    rows[3][HEADERS.index("MATCHUP")] = "BOS vs. NYK"
    with pytest.raises(ValueError, match="rows disagree on MATCHUP team pair"):
        player_logs.normalize_payload(_payload(rows), season=1984, game_type="regular")


def test_normalize_payload_requires_exactly_one_home_team_identity():
    rows = [list(row) for row in REGULAR_ROWS]
    for row in rows:
        if row[HEADERS.index("TEAM_ABBREVIATION")] == "CHI":
            row[HEADERS.index("MATCHUP")] = "CHI vs. BOS"
    with pytest.raises(ValueError, match="has 2 home team identities, expected 1"):
        player_logs.normalize_payload(_payload(rows), season=1984, game_type="regular")
