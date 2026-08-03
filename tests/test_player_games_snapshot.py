import hashlib
import json
import os
import re
import shutil

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from nba_data import cache, player_games_build, player_games_snapshot, player_logs


HEADERS = [
    "SEASON_ID", "PLAYER_ID", "PLAYER_NAME", "TEAM_ID",
    "TEAM_ABBREVIATION", "TEAM_NAME", "GAME_ID", "GAME_DATE",
    "MATCHUP", "WL", "MIN", "FGM", "FGA", "FG_PCT", "FG3M",
    "FG3A", "FG3_PCT", "FTM", "FTA", "FT_PCT", "OREB", "DREB",
    "REB", "AST", "STL", "BLK", "TOV", "PF", "PTS", "PLUS_MINUS",
]


def _source_row(
    *, season_id, game_id, game_date, player_id, team_id,
    abbreviation, team_name, matchup,
):
    return [
        season_id, player_id, f"Player {player_id}", team_id, abbreviation,
        team_name, game_id, game_date, matchup, "W", 240, 1, 2, 0.5, 0, 0,
        0.0, 0, 0, 0.0, 0, 1, 1, 1, 0, 0, 0, 1, 2, None,
    ]


def _payload(season, game_type):
    prefix = {"regular": "002", "playoff": "004"}[game_type]
    game_id = f"{prefix}{str(season - 1)[-2:]}00001"
    season_id = f"{prefix[-1]}{season - 1}"
    game_date = f"{season - 1}-10-28"
    rows = [
        _source_row(
            season_id=season_id, game_id=game_id, game_date=game_date,
            player_id=2, team_id=1610612741, abbreviation="CHI",
            team_name="Chicago Bulls", matchup="CHI @ BOS",
        ),
        _source_row(
            season_id=season_id, game_id=game_id, game_date=game_date,
            player_id=1, team_id=1610612738, abbreviation="BOS",
            team_name="Boston Celtics", matchup="BOS vs. CHI",
        ),
    ]
    return json.dumps({
        "resultSets": [{
            "name": "LeagueGameLog", "headers": HEADERS, "rowSet": rows,
        }]
    }).encode()


def _metadata(season, game_type):
    return {
        "provider": "stats_nba",
        "endpoint": "LeagueGameLog",
        "season": season,
        "season_label": f"{season - 1}-{season % 100:02d}",
        "game_type": game_type,
        "season_type": {
            "regular": "Regular Season", "playoff": "Playoffs",
        }[game_type],
        "league_id": "00",
        "player_or_team_abbreviation": "P",
    }


def _publish_raw_scope(data_dir, seasons):
    for season in seasons:
        for game_type in ("regular", "playoff"):
            destination = player_logs.cache_path(data_dir, season, game_type)
            cache.publish_bytes(
                destination,
                _payload(season, game_type),
                _metadata(season, game_type),
                player_logs.validate_payload,
            )


def _publish_references(data_dir, seasons):
    parquet_dir = data_dir / "parquet"
    parquet_dir.mkdir(parents=True, exist_ok=True)
    regular_games = []
    regular_team_games = []
    postseason_games = []
    for season in seasons:
        year = season - 1
        game_date = pd.Timestamp(f"{year}-10-28").date()
        regular_id = f"002{str(year)[-2:]}00001"
        regular_games.append({
            "game_id": regular_id,
            "season": season,
            "game_date": game_date,
            "home_franchise_id": "BOS",
            "away_franchise_id": "CHI",
            "home_pts": 2,
            "away_pts": 2,
            "neutral_site": False,
            "neutral_site_source": None,
            "venue_name": None,
            "venue_city": None,
        })
        regular_team_games.extend([
            {
                "game_id": regular_id,
                "season": season,
                "game_date": game_date,
                "franchise_id": "BOS",
                "opponent_franchise_id": "CHI",
                "is_home": True,
                "pts": 2,
            },
            {
                "game_id": regular_id,
                "season": season,
                "game_date": game_date,
                "franchise_id": "CHI",
                "opponent_franchise_id": "BOS",
                "is_home": False,
                "pts": 2,
            },
        ])
        postseason_games.append({
            "game_id": f"bref-{year}1028BOS",
            "season": season,
            "game_date": game_date,
            "game_type": "playoff",
            "home_franchise_id": "BOS",
            "away_franchise_id": "CHI",
            "home_pts": 2,
            "away_pts": 2,
            "overtime_periods": 0,
        })
    pd.DataFrame(regular_games).to_parquet(
        parquet_dir / "games.parquet", index=False
    )
    pd.DataFrame(regular_team_games).to_parquet(
        parquet_dir / "team_games.parquet", index=False
    )
    pd.DataFrame(postseason_games).to_parquet(
        parquet_dir / "playoff_games.parquet", index=False
    )
    (parquet_dir / "build_manifest.json").write_text(
        '{"fixture":"snapshot"}\n', encoding="utf-8"
    )
    (parquet_dir / "playoff_games_manifest.json").write_text(
        '{"fixture":"snapshot-playoffs"}\n', encoding="utf-8"
    )
    return parquet_dir


def _use_empty_exception_ledgers(monkeypatch, data_dir):
    """Point matchup/designation ledgers at empty fixtures under the tiny
    snapshot-fixture scope.

    Would fail if a narrowed season scope rejected the committed (real,
    wide-range) matchup/designation ledgers' entries: those ledgers are
    validated against the patched SOURCE_SEASONS, so loading the real
    ledgers under a narrowed scope would raise spurious "season outside"
    errors unrelated to what this fixture is testing.
    """
    matchup_ledger = data_dir / "fixture-matchup-exceptions.json"
    matchup_ledger.write_bytes(b'{"exceptions":{},"schema_version":1}\n')
    monkeypatch.setattr(
        player_games_build,
        "MATCHUP_EXCEPTIONS_PATH",
        matchup_ledger,
        raising=False,
    )
    designation_ledger = data_dir / "fixture-designation-exceptions.json"
    designation_ledger.write_bytes(b'{"designations":{},"schema_version":1}\n')
    monkeypatch.setattr(
        player_games_build,
        "DESIGNATION_EXCEPTIONS_PATH",
        designation_ledger,
        raising=False,
    )
    monkeypatch.setattr(player_logs, "load_matchup_exceptions", lambda *a, **k: {})
    monkeypatch.setattr(
        player_logs, "load_designation_exceptions", lambda *a, **k: {}
    )


def _write_empty_ledger(monkeypatch, data_dir):
    ledger = data_dir / "fixture-exceptions.json"
    ledger.write_text(
        json.dumps({"minutes": {}, "points": {}}, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(player_games_build, "EXCEPTIONS_PATH", ledger)
    return ledger


def _rewrite_valid_but_different_parquet(stage_dir):
    """Test-only utility: rewrite one rebuilt partition with a single
    changed player value, keeping it valid, schema-conformant Parquet.

    Simulates byte drift introduced after a rebuild without touching the
    real `build_from_roots` production path.
    """
    season_dirs = sorted(
        entry for entry in stage_dir.iterdir() if entry.is_dir()
    )
    partition_path = season_dirs[0] / "player_games.parquet"
    table = pq.read_table(partition_path)
    frame = table.to_pandas()
    frame.loc[frame.index[0], "player_name"] = "Drifted Player"
    drifted_table = pa.Table.from_pandas(
        frame,
        schema=player_games_build.PLAYER_GAMES_ARROW_SCHEMA,
        preserve_index=False,
    )
    pq.write_table(
        drifted_table,
        partition_path,
        compression="zstd",
        use_dictionary=False,
        write_statistics=True,
    )


@pytest.fixture
def snapshot_fixture(tmp_path, monkeypatch):
    """A complete two-gate fixture: a full raw archive across a source
    scope (1984, 2012, 2013) and a real trusted publication built from a
    narrower publication scope (2012, 2013).

    1984 is deliberately quarantined out of the publication scope so the
    raw-archive gate and the publication gate can be proven independent of
    one another, rather than one being an alias for the other.
    """
    source_seasons = (1984, 2012, 2013)
    publication_seasons = (2012, 2013)
    for module in (player_logs, player_games_build):
        monkeypatch.setattr(module, "SOURCE_SEASONS", source_seasons)
        monkeypatch.setattr(module, "PUBLICATION_SEASONS", publication_seasons)
        monkeypatch.setattr(module, "PLAY_IN_SEASONS", ())

    data_dir = tmp_path / "data"
    _publish_raw_scope(data_dir, source_seasons)
    reference_dir = _publish_references(data_dir, publication_seasons)
    _use_empty_exception_ledgers(monkeypatch, data_dir)
    _write_empty_ledger(monkeypatch, data_dir)

    raw_root = data_dir / "raw/stats_nba/player_logs"
    publication_root = tmp_path / "publication"
    publication_root.mkdir()
    player_games_build.build_from_roots(raw_root, reference_dir, publication_root)
    manifest_sha256 = hashlib.sha256(
        (publication_root / "_manifest.json").read_bytes()
    ).hexdigest()
    return raw_root, publication_root, reference_dir, manifest_sha256


# ---------------------------------------------------------------------------
# validate_layout
# ---------------------------------------------------------------------------


def test_validate_layout_checks_full_raw_archive_and_publication(snapshot_fixture):
    """Would fail if validate_layout stopped short of proving both the
    complete raw archive and the independent publication rebuild."""
    raw_root, publication_root, reference_dir, digest = snapshot_fixture

    report = player_games_snapshot.validate_layout(
        raw_root, publication_root, reference_dir, digest,
    )

    assert report == {
        "status": "ok",
        "archive_cache_entries": 6,
        "publication_cache_entries": 4,
        "publication_partitions": 2,
        "manifest_sha256": digest,
    }


def test_validate_layout_rejects_missing_historical_body(snapshot_fixture):
    """Would fail if the raw-archive gate only checked what the publication
    manifest happened to consume, instead of the complete source scope."""
    raw_root, publication_root, reference_dir, digest = snapshot_fixture
    historical = raw_root / "season=1984/regular.json"
    historical.unlink()

    with pytest.raises(
        player_games_snapshot.PlayerGamesSnapshotError,
        match="season=1984/regular.json",
    ):
        player_games_snapshot.validate_layout(
            raw_root,
            publication_root,
            reference_dir,
            digest,
        )


def test_validate_layout_rejects_changed_publication_partition(snapshot_fixture):
    """Would fail if validate_layout trusted the on-disk partition bytes
    instead of independently recomputing and comparing them."""
    raw_root, publication_root, reference_dir, digest = snapshot_fixture
    partition_path = publication_root / "season=2012" / "player_games.parquet"
    table = pq.read_table(partition_path)
    frame = table.to_pandas()
    frame.loc[frame.index[0], "player_name"] = "Changed Player"
    changed_table = pa.Table.from_pandas(
        frame,
        schema=player_games_build.PLAYER_GAMES_ARROW_SCHEMA,
        preserve_index=False,
    )
    pq.write_table(
        changed_table,
        partition_path,
        compression="zstd",
        use_dictionary=False,
        write_statistics=True,
    )

    with pytest.raises(
        player_games_snapshot.PlayerGamesSnapshotError,
        match="season 2012",
    ):
        player_games_snapshot.validate_layout(
            raw_root, publication_root, reference_dir, digest,
        )


def test_validate_layout_rejects_manifest_sha_mismatch(snapshot_fixture):
    """Would fail if an explicit --manifest-sha256 pin were silently ignored
    when it disagrees with the on-disk manifest bytes."""
    raw_root, publication_root, reference_dir, _digest = snapshot_fixture

    with pytest.raises(
        player_games_snapshot.PlayerGamesSnapshotError,
        match="manifest SHA-256 mismatch",
    ):
        player_games_snapshot.validate_layout(
            raw_root, publication_root, reference_dir, "0" * 64,
        )


def test_validate_layout_rejects_symlink_fifo_and_extra_layout_entries(
    snapshot_fixture,
):
    """Would fail if the layout walk followed symlinks (stat instead of
    lstat), silently tolerated special files, or ignored unexpected
    top-level publication entries."""
    raw_root, publication_root, reference_dir, digest = snapshot_fixture

    symlink_path = raw_root / "season=2012" / "regular.json.symlink"
    symlink_path.symlink_to(raw_root / "season=2012" / "regular.json")
    try:
        with pytest.raises(
            player_games_snapshot.PlayerGamesSnapshotError,
            match=re.escape("season=2012/regular.json.symlink"),
        ):
            player_games_snapshot.validate_layout(
                raw_root, publication_root, reference_dir, digest,
            )
    finally:
        symlink_path.unlink()

    fifo_path = publication_root / "season=2012" / "player_games.fifo"
    os.mkfifo(fifo_path)
    try:
        with pytest.raises(
            player_games_snapshot.PlayerGamesSnapshotError,
            match=re.escape("season=2012/player_games.fifo"),
        ):
            player_games_snapshot.validate_layout(
                raw_root, publication_root, reference_dir, digest,
            )
    finally:
        fifo_path.unlink()

    extra_dir = publication_root / "season=2099"
    extra_dir.mkdir()
    try:
        with pytest.raises(
            player_games_snapshot.PlayerGamesSnapshotError,
            match=re.escape("season=2099"),
        ):
            player_games_snapshot.validate_layout(
                raw_root, publication_root, reference_dir, digest,
            )
    finally:
        shutil.rmtree(extra_dir)


# ---------------------------------------------------------------------------
# verify_rebuild
# ---------------------------------------------------------------------------


def test_verify_rebuild_matches_complete_publication_inventory(snapshot_fixture):
    raw_root, publication_root, reference_dir, _digest = snapshot_fixture
    report = player_games_snapshot.verify_rebuild(
        raw_root,
        publication_root,
        reference_dir,
    )
    assert report["status"] == "ok"
    assert report["publication_files"] == 3  # two tiny partitions + manifest


def test_verify_rebuild_rejects_byte_drift(snapshot_fixture, monkeypatch):
    raw_root, publication_root, reference_dir, _digest = snapshot_fixture
    real_build = player_games_build.build_from_roots

    def build_with_drift(raw, references, stage):
        manifest = real_build(raw, references, stage)
        _rewrite_valid_but_different_parquet(stage)
        return manifest

    monkeypatch.setattr(
        player_games_snapshot.player_games_build,
        "build_from_roots",
        build_with_drift,
    )
    with pytest.raises(
        player_games_snapshot.PlayerGamesSnapshotError,
        match="rebuilt publication differs",
    ):
        player_games_snapshot.verify_rebuild(
            raw_root,
            publication_root,
            reference_dir,
        )


def test_verify_rebuild_removes_only_its_own_owned_stage(snapshot_fixture):
    """Would fail if verify_rebuild left its temp stage behind, or removed
    something else in the publication root's parent directory."""
    raw_root, publication_root, reference_dir, _digest = snapshot_fixture
    stage_parent = publication_root.parent
    before = {entry.name for entry in stage_parent.iterdir()}

    player_games_snapshot.verify_rebuild(raw_root, publication_root, reference_dir)

    after = {entry.name for entry in stage_parent.iterdir()}
    assert after == before
