"""Tests for box-score roster identities, validators and cache reads."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from nba_data import box_rosters, cache, pbp
from nba_data.player_games_build import PLAYER_GAMES_ARROW_SCHEMA


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests/fixtures/box_rosters"

OKC = 1610612760
HOU = 1610612745
IND = 1610612754

OPENER = box_rosters.WorklistGame(
    game_id="0022500001",
    season=2026,
    game_type="regular",
    game_date=date(2025, 10, 21),
)
FINALS = box_rosters.WorklistGame(
    game_id="0042400401",
    season=2025,
    game_type="playoff",
    game_date=date(2025, 6, 5),
)


def fixture_bytes(game_id: str, endpoint: str) -> bytes:
    """Return one committed source-shaped fixture body verbatim."""
    return (FIXTURES / f"{game_id}.{endpoint}.json").read_bytes()


def fixture_document(game_id: str, endpoint: str) -> dict:
    """Return one fixture body decoded, for building mutated variants."""
    return json.loads(fixture_bytes(game_id, endpoint))


def encode(document: object) -> bytes:
    return json.dumps(document).encode("utf-8")


def player_games_row(
    game_id: str,
    person_id: int,
    *,
    season: int,
    game_type: str,
    game_date: date,
    nba_team_id: int,
    franchise_id: str,
    team_abbreviation_raw: str,
    is_home: bool,
    **overrides: object,
) -> dict:
    """Return one full-schema ``player_games`` row with neutral statistics."""
    row = {
        "game_id": game_id,
        "person_id": person_id,
        "season": season,
        "game_type": game_type,
        "game_date": game_date,
        "player_name": f"Player {person_id}",
        "nba_team_id": nba_team_id,
        "team_name_raw": f"Team {nba_team_id}",
        "team_abbreviation_raw": team_abbreviation_raw,
        "matchup_raw": "AAA vs. BBB" if is_home else "AAA @ BBB",
        "franchise_id": franchise_id,
        "opponent_franchise_id": "OPP",
        "is_home": is_home,
        "min": 20,
        "pts": 10,
        "fgm": 4,
        "fga": 8,
        "fg3m": 1,
        "fg3a": 2,
        "ftm": 1,
        "fta": 1,
        "oreb": 1,
        "dreb": 2,
        "reb": 3,
        "ast": 2,
        "stl": 1,
        "blk": 0,
        "tov": 1,
        "pf": 2,
        "plus_minus": 3,
    }
    row.update(overrides)
    return row


def write_player_games_fixture(root: Path, rows: list[dict]) -> None:
    """Write a tiny ``player_games`` publication: partitions plus manifest."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    partitions: dict[str, dict] = {}
    for season in sorted({int(row["season"]) for row in rows}):
        season_rows = [row for row in rows if int(row["season"]) == season]
        table = pa.Table.from_pylist(season_rows, schema=PLAYER_GAMES_ARROW_SCHEMA)
        partition_dir = root / f"season={season}"
        partition_dir.mkdir()
        partition_path = partition_dir / "player_games.parquet"
        pq.write_table(table, partition_path, compression="zstd", use_dictionary=False)
        payload = partition_path.read_bytes()
        partitions[str(season)] = {
            "path": f"season={season}/player_games.parquet",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
            "rows": int(table.num_rows),
        }
    manifest = {"schema_version": 1, "partitions": partitions}
    (root / "_manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


THREE_ROWS = [
    player_games_row(
        "0022500001", 1629652, season=2026, game_type="regular",
        game_date=date(2025, 10, 21), nba_team_id=OKC, franchise_id="OKC",
        team_abbreviation_raw="OKC", is_home=True,
    ),
    player_games_row(
        "0022500001", 1630224, season=2026, game_type="regular",
        game_date=date(2025, 10, 21), nba_team_id=HOU, franchise_id="HOU",
        team_abbreviation_raw="HOU", is_home=False,
    ),
    player_games_row(
        "0042400401", 1628983, season=2025, game_type="playoff",
        game_date=date(2025, 6, 5), nba_team_id=OKC, franchise_id="OKC",
        team_abbreviation_raw="OKC", is_home=True,
    ),
]


def test_load_player_games_index_returns_distinct_games_appearances_and_team_rows(
    tmp_path,
):
    """Would fail if the worklist, appearances or team rows drifted from player_games."""
    root = tmp_path / "parquet-large/player_games"
    write_player_games_fixture(root, THREE_ROWS)

    index = box_rosters.load_player_games_index(root)

    assert index.games == (FINALS, OPENER)
    assert index.appearances["0022500001"] == frozenset({1629652, 1630224})
    assert index.appearances["0042400401"] == frozenset({1628983})
    assert index.team_rows["0022500001"][OKC] == ("OKC", "OKC", True)
    assert index.team_rows["0022500001"][HOU] == ("HOU", "HOU", False)
    assert set(index.team_rows["0042400401"]) == {OKC}
    manifest_bytes = (root / "_manifest.json").read_bytes()
    assert index.manifest_binding == {
        "path": "parquet-large/player_games/_manifest.json",
        "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "bytes": len(manifest_bytes),
    }


def test_load_player_games_index_rejects_partition_digest_mismatch(tmp_path):
    """Would fail if a partition were read without checking its manifest digest."""
    root = tmp_path / "parquet-large/player_games"
    write_player_games_fixture(root, THREE_ROWS)
    partition = root / "season=2026/player_games.parquet"
    original = partition.read_bytes()
    partition.write_bytes(original[:-1] + bytes([original[-1] ^ 0x01]))

    with pytest.raises(box_rosters.BoxRosterValidationError, match="SHA-256"):
        box_rosters.load_player_games_index(root)

    partition.write_bytes(original + b" ")
    with pytest.raises(box_rosters.BoxRosterValidationError, match="byte count"):
        box_rosters.load_player_games_index(root)


def test_request_parameters_are_the_fixed_v3_strings():
    """Would fail if the endpoint identities drifted from the fixed V3 strings."""
    assert box_rosters.request_parameters("0022500001", "traditional") == {
        "GameID": "0022500001",
        "StartPeriod": "0",
        "EndPeriod": "0",
        "StartRange": "0",
        "EndRange": "0",
        "RangeType": "0",
    }
    assert box_rosters.request_parameters("0022500001", "summary") == {
        "GameID": "0022500001",
    }
    with pytest.raises(box_rosters.BoxRosterValidationError, match="endpoint"):
        box_rosters.request_parameters("0022500001", "advanced")


def test_cache_path_and_expected_metadata(tmp_path):
    """Would fail if a cache pair were written or identified off-contract."""
    assert box_rosters.cache_path(tmp_path, 2026, "0022500001", "traditional") == (
        tmp_path / "raw/stats_nba/box_rosters/season=2026/0022500001.traditional.json"
    )
    assert box_rosters.cache_path(tmp_path, 2025, "0042400401", "summary") == (
        tmp_path / "raw/stats_nba/box_rosters/season=2025/0042400401.summary.json"
    )
    with pytest.raises(box_rosters.BoxRosterValidationError, match="season"):
        box_rosters.cache_path(tmp_path, 2011, "0021100001", "summary")

    metadata = box_rosters.expected_metadata(OPENER, "traditional")

    assert set(metadata) == {
        "provider", "resource", "endpoint", "parameters",
        "season", "game_type", "game_id",
    }
    assert metadata == {
        "provider": "stats_nba",
        "resource": "box_rosters",
        "endpoint": "BoxScoreTraditionalV3",
        "parameters": box_rosters.request_parameters("0022500001", "traditional"),
        "season": 2026,
        "game_type": "regular",
        "game_id": "0022500001",
    }
    assert box_rosters.expected_metadata(OPENER, "summary")["endpoint"] == (
        "BoxScoreSummaryV3"
    )


def _traditional_mutation(mutate):
    def build() -> bytes:
        document = fixture_document("0022500001", "traditional")
        result = mutate(document)
        return result if isinstance(result, bytes) else encode(document)
    return build


def _set_box(document, key, value):
    document["boxScoreTraditional"][key] = value


def _drop_player_key(document, key):
    del document["boxScoreTraditional"]["homeTeam"]["players"][0][key]


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param(
            _traditional_mutation(lambda d: _set_box(d, "gameId", "0022500002")),
            id="wrong-gameId",
        ),
        pytest.param(
            _traditional_mutation(lambda d: _set_box(d, "homeTeamId", 1610612737)),
            id="homeTeamId-differs-from-homeTeam",
        ),
        pytest.param(
            _traditional_mutation(
                lambda d: d["boxScoreTraditional"]["awayTeam"].__setitem__("players", [])
            ),
            id="empty-players",
        ),
        pytest.param(
            _traditional_mutation(lambda d: _drop_player_key(d, "comment")),
            id="player-missing-comment",
        ),
        pytest.param(
            _traditional_mutation(
                lambda d: d["boxScoreTraditional"]["homeTeam"]["players"][0][
                    "statistics"
                ].__setitem__("minutes", 32.1)
            ),
            id="minutes-not-a-string",
        ),
        pytest.param(
            _traditional_mutation(
                lambda d: d["boxScoreTraditional"]["homeTeam"].pop("teamCity")
            ),
            id="teamCity-missing",
        ),
        pytest.param(
            lambda: b"<!DOCTYPE html><html><body>Access Denied</body></html>",
            id="html-body",
        ),
        pytest.param(lambda: encode([{"boxScoreTraditional": {}}]), id="non-object-json"),
    ],
)
def test_validate_traditional_bytes_rejects_each_required_field(mutation):
    """Would fail if one required traditional field were not enforced."""
    with pytest.raises(box_rosters.BoxRosterValidationError):
        box_rosters.validate_traditional_bytes(mutation(), game_id="0022500001")


def _summary_mutation(mutate):
    def build() -> bytes:
        document = fixture_document("0022500001", "summary")
        result = mutate(document)
        return result if isinstance(result, bytes) else encode(document)
    return build


def _set_summary(document, key, value):
    document["boxScoreSummary"][key] = value


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param(
            _summary_mutation(lambda d: _set_summary(d, "gameId", "0022500002")),
            id="wrong-gameId",
        ),
        pytest.param(
            _summary_mutation(lambda d: _set_summary(d, "gameCode", "2025-10-21/HOUOKC")),
            id="gameCode-not-YYYYMMDD-slash-tricodes",
        ),
        pytest.param(
            _summary_mutation(lambda d: _set_summary(d, "gameTimeUTC", "tonight")),
            id="gameTimeUTC-not-iso",
        ),
        pytest.param(
            _summary_mutation(lambda d: d["boxScoreSummary"].pop("gameEt")),
            id="gameEt-missing",
        ),
        pytest.param(
            _summary_mutation(
                lambda d: d["boxScoreSummary"]["homeTeam"].__setitem__("inactives", {})
            ),
            id="inactives-not-a-list",
        ),
        pytest.param(
            _summary_mutation(
                lambda d: d["boxScoreSummary"]["homeTeam"]["inactives"][0].pop("personId")
            ),
            id="inactive-missing-personId",
        ),
        pytest.param(
            _summary_mutation(lambda d: d["boxScoreSummary"]["awayTeam"].pop("teamName")),
            id="teamName-missing",
        ),
    ],
)
def test_validate_summary_bytes_rejects_each_required_field(mutation):
    """Would fail if one required summary field were not enforced."""
    with pytest.raises(box_rosters.BoxRosterValidationError):
        box_rosters.validate_summary_bytes(mutation(), game_id="0022500001")


def test_validators_accept_the_fixture_bodies(tmp_path):
    """Would fail if the committed source-shaped fixtures drifted off-contract."""
    for game in (OPENER, FINALS):
        traditional = fixture_bytes(game.game_id, "traditional")
        summary = fixture_bytes(game.game_id, "summary")
        box_rosters.validate_traditional_bytes(traditional, game_id=game.game_id)
        box_rosters.validate_summary_bytes(summary, game_id=game.game_id)
        traditional_path = tmp_path / f"{game.game_id}.traditional.json"
        summary_path = tmp_path / f"{game.game_id}.summary.json"
        traditional_path.write_bytes(traditional)
        summary_path.write_bytes(summary)
        box_rosters.validate_traditional(traditional_path, game_id=game.game_id)
        box_rosters.validate_summary(summary_path, game_id=game.game_id)
        with pytest.raises(box_rosters.BoxRosterValidationError, match="gameId"):
            box_rosters.validate_traditional(traditional_path, game_id="0022500099")
        with pytest.raises(box_rosters.BoxRosterValidationError, match="gameId"):
            box_rosters.validate_summary(summary_path, game_id="0022500099")


def _publish(data_dir: Path, game, endpoint: str) -> Path:
    destination = box_rosters.cache_path(data_dir, game.season, game.game_id, endpoint)
    validator = (
        box_rosters.validate_traditional
        if endpoint == "traditional"
        else box_rosters.validate_summary
    )
    cache.publish_bytes(
        destination,
        fixture_bytes(game.game_id, endpoint),
        box_rosters.expected_metadata(game, endpoint),
        lambda path: validator(path, game_id=game.game_id),
    )
    return destination


def _sidecar(destination: Path) -> Path:
    return destination.with_name(destination.name + ".meta.json")


def test_read_cached_accepts_only_exact_identity(tmp_path):
    """Would fail if a pair fetched for other parameters passed as this identity."""
    assert box_rosters.read_cached(tmp_path, OPENER, "traditional") is None
    destination = _publish(tmp_path, OPENER, "traditional")

    payload, metadata = box_rosters.read_cached(tmp_path, OPENER, "traditional")

    expected = box_rosters.expected_metadata(OPENER, "traditional")
    assert payload == fixture_bytes("0022500001", "traditional")
    assert {key: metadata[key] for key in expected} == expected
    assert metadata["content_length"] == len(payload)

    sidecar = _sidecar(destination)
    record = json.loads(sidecar.read_text())
    record["parameters"] = {**record["parameters"], "RangeType": "1"}
    sidecar.write_text(json.dumps(record))
    with pytest.raises(cache.CacheIntegrityError, match="metadata mismatch"):
        box_rosters.read_cached(tmp_path, OPENER, "traditional")

    record = json.loads(sidecar.read_text())
    record["parameters"] = box_rosters.request_parameters("0022500001", "traditional")
    del record["game_type"]
    sidecar.write_text(json.dumps(record))
    with pytest.raises(cache.CacheIntegrityError, match="metadata mismatch"):
        box_rosters.read_cached(tmp_path, OPENER, "traditional")


def test_read_cached_rejects_incomplete_pair_and_mismatched_content_length(tmp_path):
    """Would fail if a partial pair or an unbound body length counted as a hit."""
    summary = _publish(tmp_path, OPENER, "summary")
    _sidecar(summary).unlink()
    with pytest.raises(cache.CacheIntegrityError, match="incomplete cache pair"):
        box_rosters.read_cached(tmp_path, OPENER, "summary")

    traditional = _publish(tmp_path, OPENER, "traditional")
    sidecar = _sidecar(traditional)
    record = json.loads(sidecar.read_text())
    record["content_length"] += 1
    sidecar.write_text(json.dumps(record))
    with pytest.raises(cache.CacheIntegrityError, match="content length mismatch"):
        box_rosters.read_cached(tmp_path, OPENER, "traditional")


def test_validate_cache_inventory_requires_both_pairs_per_game_and_no_extra_files(
    tmp_path,
):
    """Would fail if a missing pair, a stray file or an unsafe entry passed."""
    games = (FINALS, OPENER)
    bodies = tuple(
        _publish(tmp_path, game, endpoint)
        for game in games
        for endpoint in ("traditional", "summary")
    )
    root = tmp_path / "raw/stats_nba/box_rosters"

    assert box_rosters.validate_cache_inventory(tmp_path, games) == bodies
    assert bodies[0] == root / "season=2025/0042400401.traditional.json"

    extra = root / "season=2026/0022500002.summary.json"
    extra.write_bytes(b"{}")
    with pytest.raises(cache.CacheIntegrityError, match="unexpected"):
        box_rosters.validate_cache_inventory(tmp_path, games)
    extra.unlink()

    (root / "season=2024").mkdir()
    with pytest.raises(cache.CacheIntegrityError, match="unexpected"):
        box_rosters.validate_cache_inventory(tmp_path, games)
    (root / "season=2024").rmdir()

    link = root / "season=2026/0022500001.summary.json.meta.json"
    target = link.with_name("moved.meta.json")
    link.rename(target)
    link.symlink_to(target)
    with pytest.raises(cache.CacheIntegrityError, match="unsafe|unexpected"):
        box_rosters.validate_cache_inventory(tmp_path, games)
    link.unlink()
    target.rename(link)
    assert box_rosters.validate_cache_inventory(tmp_path, games) == bodies

    summary = root / "season=2026/0022500001.summary.json"
    summary.unlink()
    _sidecar(summary).unlink()
    with pytest.raises(cache.CacheIntegrityError, match="missing"):
        box_rosters.validate_cache_inventory(tmp_path, games)

    _publish(tmp_path, OPENER, "summary")
    unfetched = box_rosters.WorklistGame(
        "0022500003", 2026, "regular", date(2025, 10, 22)
    )
    with pytest.raises(cache.CacheIntegrityError, match="missing"):
        box_rosters.validate_cache_inventory(tmp_path, (FINALS, OPENER, unfetched))


def test_game_keys_maps_date_and_tricodes_to_game_id():
    """Would fail if the (date, away, home) key did not come from player_games rows."""
    index = box_rosters.PlayerGamesIndex(
        manifest_binding={
            "path": "parquet-large/player_games/_manifest.json",
            "sha256": "0" * 64,
            "bytes": 2,
        },
        games=(OPENER,),
        appearances={"0022500001": frozenset({1629652, 1630224})},
        team_rows={
            "0022500001": {
                OKC: ("OKC", "OKC", True),
                HOU: ("HOU", "HOU", False),
            }
        },
    )

    assert box_rosters.game_keys(index) == {
        (date(2025, 10, 21), "HOU", "OKC"): "0022500001",
    }

    one_sided = box_rosters.PlayerGamesIndex(
        manifest_binding=index.manifest_binding,
        games=(FINALS,),
        appearances={"0042400401": frozenset({1628983})},
        team_rows={"0042400401": {OKC: ("OKC", "OKC", True)}},
    )
    with pytest.raises(box_rosters.BoxRosterValidationError, match="home and away"):
        box_rosters.game_keys(one_sided)


# --------------------------------------------------------------------------
# fetch
# --------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code, content, headers=None, url=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self.url = url or "https://stats.nba.com/stats/x"


class FakeSession:
    """Queues responses/exceptions in order and records every call made."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.request_log = []

    def get(self, url, *, params=None, headers=None, timeout=None, allow_redirects=None):
        self.calls += 1
        self.request_log.append({
            "url": url,
            "params": params,
            "headers": headers,
            "timeout": timeout,
            "allow_redirects": allow_redirects,
        })
        if not self._responses:
            raise AssertionError("FakeSession received more calls than queued responses")
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeClock:
    def __init__(self):
        self.value = 0.0
        self.sleeps = []

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds


def _no_wait_pacer():
    return pbp.RequestPacer(now=lambda: 0.0, sleeper=lambda seconds: None)


def _ok(game, endpoint: str) -> FakeResponse:
    return FakeResponse(
        200,
        fixture_bytes(game.game_id, endpoint),
        {},
        f"https://stats.nba.com/stats/{box_rosters.ENDPOINTS[endpoint]}?GameID={game.game_id}",
    )


def _pair_responses(*games) -> list:
    return [_ok(game, endpoint) for game in games for endpoint in ("traditional", "summary")]


def _fail_sleeper(seconds):
    raise AssertionError(f"unexpected retry sleep of {seconds} s")


def _fetch_environment(tmp_path, monkeypatch, *, rows=THREE_ROWS):
    """Write a tiny worklist publication and fake the capacity and DNS gates."""
    write_player_games_fixture(tmp_path / box_rosters.PLAYER_GAMES_SUBDIR, rows)
    events = []
    monkeypatch.setattr(
        box_rosters.player_logs,
        "available_bytes",
        lambda path: events.append(("capacity", Path(path))) or box_rosters.FETCH_HEADROOM_BYTES,
    )
    monkeypatch.setattr(
        box_rosters.stats_nba,
        "preflight_dns",
        lambda: events.append(("preflight", None)) or ("203.0.113.1",),
    )
    return events


def test_fetch_requests_two_identities_per_uncached_game_in_worklist_order(
    tmp_path, monkeypatch
):
    """Would fail if an identity, its URL, its parameters or its sidecar drifted."""
    _fetch_environment(tmp_path, monkeypatch)
    session = FakeSession(_pair_responses(FINALS, OPENER))

    box_rosters.fetch(
        tmp_path, session=session, pacer=_no_wait_pacer(), sleeper=_fail_sleeper
    )

    assert session.calls == 4
    expected_requests = [
        (game, endpoint)
        for game in (FINALS, OPENER)
        for endpoint in ("traditional", "summary")
    ]
    for entry, (game, endpoint) in zip(session.request_log, expected_requests):
        assert entry["url"] == (
            f"https://stats.nba.com/stats/{box_rosters.ENDPOINTS[endpoint]}"
        )
        assert entry["params"] == sorted(
            box_rosters.request_parameters(game.game_id, endpoint).items()
        )
        assert entry["allow_redirects"] is False
        assert entry["timeout"] == 90
    assert [entry["url"] for entry in session.request_log] == [
        "https://stats.nba.com/stats/boxscoretraditionalv3",
        "https://stats.nba.com/stats/boxscoresummaryv3",
        "https://stats.nba.com/stats/boxscoretraditionalv3",
        "https://stats.nba.com/stats/boxscoresummaryv3",
    ]
    for game, endpoint in expected_requests:
        destination = box_rosters.cache_path(
            tmp_path, game.season, game.game_id, endpoint
        )
        assert destination.is_file()
        metadata = json.loads(_sidecar(destination).read_text(encoding="utf-8"))
        expected = box_rosters.expected_metadata(game, endpoint)
        assert {key: metadata[key] for key in expected} == expected
        payload, _ = box_rosters.read_cached(tmp_path, game, endpoint)
        assert payload == fixture_bytes(game.game_id, endpoint)
    assert box_rosters.validate_cache_inventory(tmp_path, (FINALS, OPENER))


def test_fetch_skips_complete_valid_pairs_and_fails_on_a_damaged_pair(
    tmp_path, monkeypatch
):
    """Would fail if a valid pair were refetched or a damaged pair repaired."""
    events = _fetch_environment(tmp_path, monkeypatch)
    opener_traditional = _publish(tmp_path, OPENER, "traditional")
    opener_summary = _publish(tmp_path, OPENER, "summary")
    before = (
        opener_traditional.read_bytes(),
        _sidecar(opener_traditional).read_bytes(),
        opener_summary.read_bytes(),
        _sidecar(opener_summary).read_bytes(),
    )

    damaged = box_rosters.cache_path(tmp_path, FINALS.season, FINALS.game_id, "summary")
    damaged.parent.mkdir(parents=True, exist_ok=True)
    damaged.write_bytes(fixture_bytes(FINALS.game_id, "summary"))
    session = FakeSession([])
    with pytest.raises(cache.CacheIntegrityError, match="incomplete cache pair"):
        box_rosters.fetch(
            tmp_path, session=session, pacer=_no_wait_pacer(), sleeper=_fail_sleeper
        )
    assert session.calls == 0
    assert events == []
    assert damaged.read_bytes() == fixture_bytes(FINALS.game_id, "summary")
    damaged.unlink()

    tampered = _publish(tmp_path, FINALS, "traditional")
    sidecar = _sidecar(tampered)
    record = json.loads(sidecar.read_text())
    record["game_type"] = "regular"
    sidecar.write_text(json.dumps(record))
    with pytest.raises(cache.CacheIntegrityError, match="metadata mismatch"):
        box_rosters.fetch(
            tmp_path, session=session, pacer=_no_wait_pacer(), sleeper=_fail_sleeper
        )
    assert session.calls == 0
    assert events == []
    tampered.unlink()
    sidecar.unlink()

    session = FakeSession(_pair_responses(FINALS))
    box_rosters.fetch(
        tmp_path, session=session, pacer=_no_wait_pacer(), sleeper=_fail_sleeper
    )

    assert session.calls == 2
    assert [dict(entry["params"])["GameID"] for entry in session.request_log] == [
        FINALS.game_id, FINALS.game_id,
    ]
    assert (
        opener_traditional.read_bytes(),
        _sidecar(opener_traditional).read_bytes(),
        opener_summary.read_bytes(),
        _sidecar(opener_summary).read_bytes(),
    ) == before
    assert box_rosters.validate_cache_inventory(tmp_path, (FINALS, OPENER))


def test_fetch_filters_seasons_and_deduplicates(tmp_path, monkeypatch):
    """Would fail if the season filter leaked or a repeated season fetched twice."""
    _fetch_environment(tmp_path, monkeypatch)

    session = FakeSession(_pair_responses(OPENER))
    box_rosters.fetch(
        tmp_path, [2026, 2026], session=session, pacer=_no_wait_pacer(),
        sleeper=_fail_sleeper,
    )
    assert session.calls == 2
    assert {dict(entry["params"])["GameID"] for entry in session.request_log} == {
        OPENER.game_id,
    }
    assert box_rosters.read_cached(tmp_path, FINALS, "traditional") is None
    assert box_rosters.read_cached(tmp_path, FINALS, "summary") is None

    session = FakeSession(_pair_responses(FINALS))
    box_rosters.fetch(
        tmp_path, iter([2025, 2026, 2025]), session=session, pacer=_no_wait_pacer(),
        sleeper=_fail_sleeper,
    )
    assert session.calls == 2
    assert {dict(entry["params"])["GameID"] for entry in session.request_log} == {
        FINALS.game_id,
    }

    session = FakeSession([])
    box_rosters.fetch(
        tmp_path, session=session, pacer=_no_wait_pacer(), sleeper=_fail_sleeper
    )
    assert session.calls == 0

    with pytest.raises(box_rosters.BoxRosterValidationError, match="season"):
        box_rosters.fetch(
            tmp_path, [2011], session=session, pacer=_no_wait_pacer(),
            sleeper=_fail_sleeper,
        )
    assert session.calls == 0


def test_fetch_preflights_once_and_checks_capacity_once_before_the_first_request(
    tmp_path, monkeypatch
):
    """Would fail if the gates ran per identity, after a request, or with no work."""
    events = _fetch_environment(tmp_path, monkeypatch)

    class RecordingSession(FakeSession):
        def get(self, url, **kwargs):
            events.append(("request", url))
            return super().get(url, **kwargs)

    session = RecordingSession(_pair_responses(FINALS, OPENER))
    box_rosters.fetch(
        tmp_path, session=session, pacer=_no_wait_pacer(), sleeper=_fail_sleeper
    )

    assert session.calls == 4
    assert [kind for kind, _ in events] == [
        "capacity", "preflight", "request", "request", "request", "request",
    ]
    assert events[0] == ("capacity", tmp_path)

    events.clear()
    session = RecordingSession([])
    box_rosters.fetch(
        tmp_path, session=session, pacer=_no_wait_pacer(), sleeper=_fail_sleeper
    )
    assert events == []

    other = tmp_path / "other"
    write_player_games_fixture(other / box_rosters.PLAYER_GAMES_SUBDIR, THREE_ROWS)
    monkeypatch.setattr(
        box_rosters.player_logs,
        "available_bytes",
        lambda path: events.append(("capacity", Path(path)))
        or box_rosters.FETCH_HEADROOM_BYTES - 1,
    )
    session = RecordingSession([])
    with pytest.raises(
        box_rosters.BoxRosterValidationError, match="insufficient fetch capacity"
    ):
        box_rosters.fetch(
            other, session=session, pacer=_no_wait_pacer(), sleeper=_fail_sleeper
        )
    assert events == [("capacity", other)]
    assert session.calls == 0
    assert not (other / box_rosters.RAW_SUBDIR).exists()


def test_fetch_retries_transient_statuses_then_stops_and_never_retries_other_4xx(
    tmp_path, monkeypatch
):
    """Would fail if 5xx were not retried on the fixed ladder or a 404 were retried."""
    _fetch_environment(tmp_path, monkeypatch)
    sleeps = []
    session = FakeSession([
        FakeResponse(503, b"", {}),
        FakeResponse(503, b"", {}),
        *_pair_responses(FINALS, OPENER),
    ])

    box_rosters.fetch(
        tmp_path, session=session, pacer=_no_wait_pacer(), sleeper=sleeps.append
    )

    assert sleeps == [5.0, 10.0]
    assert session.calls == 6
    assert box_rosters.validate_cache_inventory(tmp_path, (FINALS, OPENER))

    other = tmp_path / "other"
    write_player_games_fixture(other / box_rosters.PLAYER_GAMES_SUBDIR, THREE_ROWS)
    sleeps = []
    session = FakeSession([FakeResponse(404, b"{}", {}), *_pair_responses(FINALS)])
    with pytest.raises(cache.SourceAccessError, match="HTTP 404"):
        box_rosters.fetch(
            other, session=session, pacer=_no_wait_pacer(), sleeper=sleeps.append
        )
    assert sleeps == []
    assert session.calls == 1
    assert box_rosters.read_cached(other, FINALS, "traditional") is None


def test_fetch_stops_without_retry_on_a_schema_invalid_200(tmp_path, monkeypatch):
    """Would fail if a block page or HTML 200 were retried or cached."""
    _fetch_environment(tmp_path, monkeypatch)
    sleeps = []
    html = b"<!DOCTYPE html><html><body>Access Denied</body></html>"
    session = FakeSession([FakeResponse(200, html, {}), *_pair_responses(FINALS, OPENER)])

    with pytest.raises(box_rosters.BoxRosterValidationError, match="JSON"):
        box_rosters.fetch(
            tmp_path, session=session, pacer=_no_wait_pacer(), sleeper=sleeps.append
        )

    assert sleeps == []
    assert session.calls == 1
    for game in (FINALS, OPENER):
        for endpoint in ("traditional", "summary"):
            destination = box_rosters.cache_path(
                tmp_path, game.season, game.game_id, endpoint
            )
            assert not destination.exists()
            assert not _sidecar(destination).exists()
            assert box_rosters.read_cached(tmp_path, game, endpoint) is None


def test_fetch_paces_requests_at_least_1_5_seconds_apart(tmp_path, monkeypatch):
    """Would fail if consecutive request starts were closer than 1.5 s."""
    _fetch_environment(tmp_path, monkeypatch)
    clock = FakeClock()
    pacer = pbp.RequestPacer(now=clock.now, sleeper=clock.sleep)
    session = FakeSession(_pair_responses(FINALS, OPENER))

    box_rosters.fetch(tmp_path, session=session, pacer=pacer, sleeper=_fail_sleeper)

    assert session.calls == 4
    assert clock.sleeps == [1.5, 1.5, 1.5]
    assert pacer.minimum_interval_s == box_rosters.MIN_REQUEST_INTERVAL_S == 1.5
