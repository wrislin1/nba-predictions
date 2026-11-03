import dataclasses
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from nba_data import cache, player_games_build, player_games_snapshot, player_logs


REPO_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_SCRIPT = REPO_ROOT / "scripts/archive_player_games_snapshot.sh"
RESTORE_SCRIPT = REPO_ROOT / "scripts/restore_player_games_snapshot.sh"


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


# ---------------------------------------------------------------------------
# scripts/archive_player_games_snapshot.sh and
# scripts/restore_player_games_snapshot.sh
#
# These scripts are exercised as real subprocesses: filesystem
# orchestration, capacity gates, atomic renames, and the restore rollback
# state machine are the things under test, not `nba_data.player_games_snapshot`
# itself (already covered above as direct in-process calls).
#
# `snapshot_fixture` builds its tiny two-scope tree by monkeypatching
# `SOURCE_SEASONS`/`PUBLICATION_SEASONS`/`PLAY_IN_SEASONS` *in this test
# process*. A subprocess re-imports `nba_data.player_logs`/
# `player_games_build` from scratch with their real, full (1984-2026)
# season scope and real packaged exception ledgers, so `monkeypatch` alone
# cannot make a subprocess agree the tiny fixture tree is a complete
# archive. `_write_season_scope_shim` below reproduces the same
# monkeypatches for a freshly spawned interpreter via a dynamically
# generated (never committed) `sitecustomize.py`.
# ---------------------------------------------------------------------------

_SUBPROCESS_SOURCE_SEASONS = (1984, 2012, 2013)
_SUBPROCESS_PUBLICATION_SEASONS = (2012, 2013)


def _write_season_scope_shim(tmp_path):
    """Write a `sitecustomize.py`, importable via `PYTHONPATH`, that patches
    the narrow fixture season scope and empty exception ledgers into a
    freshly started Python interpreter before any of its own imports run.

    `site.py` imports `sitecustomize` during interpreter startup -- before
    `-m nba_data.player_games_snapshot`'s own
    `from nba_data import player_logs, player_games_build` executes -- so
    that later import simply observes the already-patched module objects
    via `sys.modules`.
    """
    shim_dir = tmp_path / "season_scope_shim"
    shim_dir.mkdir(exist_ok=True)
    matchup_ledger = shim_dir / "matchup-exceptions.json"
    matchup_ledger.write_bytes(b'{"exceptions":{},"schema_version":1}\n')
    designation_ledger = shim_dir / "designation-exceptions.json"
    designation_ledger.write_bytes(b'{"designations":{},"schema_version":1}\n')
    exceptions_ledger = shim_dir / "exceptions.json"
    exceptions_ledger.write_text(
        json.dumps({"minutes": {}, "points": {}}, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    (shim_dir / "sitecustomize.py").write_text(
        "from pathlib import Path\n"
        "from nba_data import player_logs, player_games_build\n"
        f"player_logs.SOURCE_SEASONS = {_SUBPROCESS_SOURCE_SEASONS!r}\n"
        f"player_logs.PUBLICATION_SEASONS = {_SUBPROCESS_PUBLICATION_SEASONS!r}\n"
        "player_logs.PLAY_IN_SEASONS = ()\n"
        "player_games_build.SOURCE_SEASONS = player_logs.SOURCE_SEASONS\n"
        "player_games_build.PUBLICATION_SEASONS = player_logs.PUBLICATION_SEASONS\n"
        "player_games_build.PLAY_IN_SEASONS = player_logs.PLAY_IN_SEASONS\n"
        f"player_games_build.MATCHUP_EXCEPTIONS_PATH = Path({str(matchup_ledger)!r})\n"
        "player_games_build.DESIGNATION_EXCEPTIONS_PATH = "
        f"Path({str(designation_ledger)!r})\n"
        f"player_games_build.EXCEPTIONS_PATH = Path({str(exceptions_ledger)!r})\n"
        "player_logs.load_matchup_exceptions = lambda *a, **k: {}\n"
        "player_logs.load_designation_exceptions = lambda *a, **k: {}\n"
    )
    return shim_dir


def _materialize_data_dir(snapshot_fixture_result):
    """Relocate `snapshot_fixture`'s standalone publication into the
    `$data_dir/parquet-large/player_games` layout the archive/restore
    scripts expect. The fixture's raw archive and reference dir are
    already at `$data_dir/raw/stats_nba/player_logs` and `$data_dir/parquet`
    respectively."""
    raw_root, publication_root, reference_dir, manifest_sha256 = snapshot_fixture_result
    data_dir = raw_root.parents[2]
    destination = data_dir / "parquet-large" / "player_games"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(publication_root, destination)
    return data_dir, reference_dir, manifest_sha256


def _default_capacity_shim(base_dir: Path) -> Path:
    """A PATH-shimmed `du`/`df` reporting a generous fixed capacity.

    The real capacity probe (`required = allocated source bytes + archive
    filesystem total bytes / 5`) uses the archive root's *filesystem* total,
    which on this host (`/`, 196 GB total) demands ~39 GB free regardless of
    how tiny the fixture actually is. Every archive test that expects a
    real copy to proceed installs this generous, fixed, PATH-shimmed probe
    so success depends on the script's own logic, not on how much free
    space `/` happens to have; the dedicated capacity test below installs
    its own controllable shim instead, taking priority over this default.
    """
    shim_dir = base_dir / ".default_capacity_shim"
    if shim_dir.is_dir():
        return shim_dir
    shim_dir.mkdir(parents=True, exist_ok=True)
    du_script = shim_dir / "du"
    du_script.write_text(
        "#!/usr/bin/env bash\n"
        "for arg in \"$@\"; do\n"
        "  case \"$arg\" in -*) continue ;; esac\n"
        "  printf '%d\\t%s\\n' 1 \"$arg\"\n"
        "done\n"
    )
    du_script.chmod(0o755)
    df_script = shim_dir / "df"
    df_script.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'Size Avail\\n'\n"
        "printf '%d %d\\n' 1099511627776 1099511627776\n"
    )
    df_script.chmod(0o755)
    return shim_dir


def _run_archive(data_dir, archive_root, shim_dir, *, extra_env=None, path_prefix=None):
    env = dict(os.environ)
    env["NBA_PLAYER_GAMES_DATA_DIR"] = str(data_dir)
    env["NBA_PLAYER_GAMES_ARCHIVE_ROOT"] = str(archive_root)
    env["PYTHONPATH"] = str(shim_dir)
    default_capacity = _default_capacity_shim(Path(archive_root).parent)
    prefixes = [str(path_prefix)] if path_prefix is not None else []
    prefixes.append(str(default_capacity))
    env["PATH"] = ":".join(prefixes) + f":{env.get('PATH', '')}"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(ARCHIVE_SCRIPT)],
        env=env,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )


def _make_capacity_shims(shim_dir: Path) -> Path:
    shim_dir.mkdir(parents=True, exist_ok=True)
    du_script = shim_dir / "du"
    du_script.write_text(
        "#!/usr/bin/env bash\n"
        "for arg in \"$@\"; do\n"
        "  case \"$arg\" in -*) continue ;; esac\n"
        "  printf '%d\\t%s\\n' \"${FAKE_DU_BYTES_PER_PATH:-0}\" \"$arg\"\n"
        "done\n"
    )
    du_script.chmod(0o755)
    df_script = shim_dir / "df"
    df_script.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'Size Avail\\n'\n"
        "printf '%d %d\\n' \"${FAKE_DF_TOTAL_BYTES:-0}\" \"${FAKE_DF_AVAIL_BYTES:-0}\"\n"
    )
    df_script.chmod(0o755)
    return shim_dir


@dataclasses.dataclass(frozen=True)
class ArchivedSnapshot:
    archive_root: Path
    name: str
    manifest_sha256: str
    reference_dir: Path
    shim_dir: Path
    data_dir: Path


@pytest.fixture
def archived_snapshot(snapshot_fixture, tmp_path):
    """Produce a real, verified snapshot by running the real archive script
    against a materialized copy of `snapshot_fixture`."""
    data_dir, reference_dir, manifest_sha256 = _materialize_data_dir(snapshot_fixture)
    shim_dir = _write_season_scope_shim(tmp_path)
    archive_root = tmp_path / "archive_root"
    archive_root.mkdir()

    result = _run_archive(data_dir, archive_root, shim_dir)
    assert result.returncode == 0, result.stderr

    name = f"player-games-archive-2026-{manifest_sha256[:12]}"
    assert (archive_root / name).is_dir()
    return ArchivedSnapshot(
        archive_root=archive_root,
        name=name,
        manifest_sha256=manifest_sha256,
        reference_dir=reference_dir,
        shim_dir=shim_dir,
        data_dir=data_dir,
    )


def _run_restore_script(archived_snapshot, restore_root, *, extra_env=None, path_prefix=None):
    env = dict(os.environ)
    env["NBA_PLAYER_GAMES_ARCHIVE_ROOT"] = str(archived_snapshot.archive_root)
    env["NBA_PLAYER_GAMES_DATA_DIR"] = str(restore_root)
    env["NBA_PLAYER_GAMES_REFERENCE_DIR"] = str(archived_snapshot.reference_dir)
    env["PYTHONPATH"] = str(archived_snapshot.shim_dir)
    if path_prefix is not None:
        env["PATH"] = f"{path_prefix}:{env.get('PATH', '')}"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(RESTORE_SCRIPT), archived_snapshot.name],
        env=env,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )


# ---------------------------------------------------------------------------
# Archive behaviors
# ---------------------------------------------------------------------------


def test_archive_snapshot_copies_full_raw_and_trusted_publication(snapshot_fixture, tmp_path):
    """Would fail if the archive silently dropped the historical raw pair
    that the (narrower) publication manifest never consumed, or produced a
    checksum inventory that didn't literally match every archived file."""
    data_dir, reference_dir, manifest_sha256 = _materialize_data_dir(snapshot_fixture)
    shim_dir = _write_season_scope_shim(tmp_path)
    archive_root = tmp_path / "archive_root"
    archive_root.mkdir()

    result = _run_archive(data_dir, archive_root, shim_dir)

    assert result.returncode == 0, result.stderr
    assert manifest_sha256 in result.stdout
    name = f"player-games-archive-2026-{manifest_sha256[:12]}"
    snapshot = archive_root / name
    assert snapshot.is_dir()

    raw_dir = snapshot / "raw/stats_nba/player_logs"
    publication_dir = snapshot / "parquet-large/player_games"
    assert raw_dir.is_dir()
    assert publication_dir.is_dir()

    # The historical (1984) raw pair is part of the source scope but
    # outside the (2012, 2013) publication scope: it must still be archived.
    assert (raw_dir / "season=1984/regular.json").is_file()
    assert (raw_dir / "season=1984/regular.json.meta.json").is_file()
    assert (raw_dir / "season=1984/playoff.json").is_file()
    assert (raw_dir / "season=1984/playoff.json.meta.json").is_file()
    manifest_text = (publication_dir / "_manifest.json").read_text()
    assert "1984" not in json.loads(manifest_text)["scope"]["ending_seasons"].__str__()

    sums_path = snapshot / "SHA256SUMS"
    lines = sums_path.read_text().splitlines()
    listed_paths = [line.split("  ", 1)[1] for line in lines]
    assert listed_paths == sorted(listed_paths)
    assert "SHA256SUMS" not in listed_paths

    expected_pairs = sorted(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(snapshot).as_posix()}"
        for path in snapshot.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    )
    assert sorted(lines) == expected_pairs

    subprocess.run(
        ["sha256sum", "--check", "--strict", "SHA256SUMS"],
        cwd=snapshot, check=True, capture_output=True, text=True,
    )


def test_archive_snapshot_is_idempotent_only_for_byte_identical_final(
    snapshot_fixture, tmp_path,
):
    data_dir, _reference_dir, manifest_sha256 = _materialize_data_dir(snapshot_fixture)
    shim_dir = _write_season_scope_shim(tmp_path)
    archive_root = tmp_path / "archive_root"
    archive_root.mkdir()

    first = _run_archive(data_dir, archive_root, shim_dir)
    assert first.returncode == 0, first.stderr
    name = f"player-games-archive-2026-{manifest_sha256[:12]}"
    snapshot = archive_root / name
    mtime_before = snapshot.stat().st_mtime_ns

    second = _run_archive(data_dir, archive_root, shim_dir)

    assert second.returncode == 0, second.stderr
    assert snapshot.stat().st_mtime_ns == mtime_before
    assert [p.name for p in archive_root.iterdir()] == [name]


def test_archive_snapshot_rejects_different_existing_final(snapshot_fixture, tmp_path):
    data_dir, _reference_dir, manifest_sha256 = _materialize_data_dir(snapshot_fixture)
    shim_dir = _write_season_scope_shim(tmp_path)
    archive_root = tmp_path / "archive_root"
    archive_root.mkdir()

    first = _run_archive(data_dir, archive_root, shim_dir)
    assert first.returncode == 0, first.stderr
    name = f"player-games-archive-2026-{manifest_sha256[:12]}"
    snapshot = archive_root / name

    sums_path = snapshot / "SHA256SUMS"
    lines = sums_path.read_text().splitlines()
    digest, rest = lines[0].split("  ", 1)
    tampered_digest = ("0" if digest[0] != "0" else "1") + digest[1:]
    lines[0] = f"{tampered_digest}  {rest}"
    tampered_text = "\n".join(lines) + "\n"
    sums_path.write_text(tampered_text)

    second = _run_archive(data_dir, archive_root, shim_dir)

    assert second.returncode != 0
    # Never modify a published snapshot -- the tampering must survive.
    assert sums_path.read_text() == tampered_text
    assert [p.name for p in archive_root.iterdir()] == [name]


def test_archive_snapshot_rejects_stale_stage(snapshot_fixture, tmp_path):
    data_dir, _reference_dir, _manifest_sha256 = _materialize_data_dir(snapshot_fixture)
    shim_dir = _write_season_scope_shim(tmp_path)
    archive_root = tmp_path / "archive_root"
    archive_root.mkdir()
    stale = archive_root / ".player-games-archive.stale123"
    stale.mkdir()
    (stale / "leftover.txt").write_text("from a crashed prior run\n")

    result = _run_archive(data_dir, archive_root, shim_dir)

    assert result.returncode != 0
    assert "stale" in result.stderr.lower()
    # The stale entry is rejected, never silently absorbed or deleted.
    assert stale.is_dir()
    assert (stale / "leftover.txt").exists()
    assert [p.name for p in archive_root.iterdir()] == [stale.name]


def test_archive_snapshot_checks_capacity_before_copy(snapshot_fixture, tmp_path):
    data_dir, _reference_dir, manifest_sha256 = _materialize_data_dir(snapshot_fixture)
    shim_dir = _write_season_scope_shim(tmp_path)
    capacity_shim_dir = _make_capacity_shims(tmp_path / "capacity_shims")
    name = f"player-games-archive-2026-{manifest_sha256[:12]}"

    per_path = 500
    total = 500
    required = per_path * 2 + total // 5  # 1000 + 100 = 1100

    reject_root = tmp_path / "archive_root_reject"
    reject_root.mkdir()
    reject = _run_archive(
        data_dir, reject_root, shim_dir,
        path_prefix=str(capacity_shim_dir),
        extra_env={
            "FAKE_DU_BYTES_PER_PATH": str(per_path),
            "FAKE_DF_TOTAL_BYTES": str(total),
            "FAKE_DF_AVAIL_BYTES": str(required - 1),
        },
    )
    assert reject.returncode != 0
    assert "capacity" in reject.stderr.lower()
    assert not any(reject_root.iterdir())

    accept_root = tmp_path / "archive_root_accept"
    accept_root.mkdir()
    accept = _run_archive(
        data_dir, accept_root, shim_dir,
        path_prefix=str(capacity_shim_dir),
        extra_env={
            "FAKE_DU_BYTES_PER_PATH": str(per_path),
            "FAKE_DF_TOTAL_BYTES": str(total),
            "FAKE_DF_AVAIL_BYTES": str(required),
        },
    )
    assert accept.returncode == 0, accept.stderr
    assert (accept_root / name).is_dir()


def test_archive_snapshot_detects_source_mutation_during_copy(snapshot_fixture, tmp_path):
    """Would fail if the archive trusted the bytes it already copied instead
    of independently revalidating the *live* source after the copy."""
    data_dir, _reference_dir, _manifest_sha256 = _materialize_data_dir(snapshot_fixture)
    shim_dir = _write_season_scope_shim(tmp_path)
    archive_root = tmp_path / "archive_root"
    archive_root.mkdir()

    mutate_shim_dir = tmp_path / "mutate_shim"
    mutate_shim_dir.mkdir()
    real_sha256sum = shutil.which("sha256sum")
    target_file = data_dir / "raw/stats_nba/player_logs/season=2012/regular.json"
    original_bytes = target_file.read_bytes()
    # `sha256sum --check --strict SHA256SUMS` runs exactly once for a fresh
    # archive, immediately after the stage's own checksum file is written
    # and immediately before the live source is revalidated: mutating the
    # live source right there lands the mutation squarely inside the
    # already-completed copy, undetectable by anything that only re-checks
    # the stage.
    shim_script = mutate_shim_dir / "sha256sum"
    shim_script.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$#\" -eq 3 ] && [ \"$1\" = \"--check\" ] && [ \"$2\" = \"--strict\" ] "
        "&& [ \"$3\" = \"SHA256SUMS\" ]; then\n"
        f"  printf '{{\"resultSets\": []}}' > {shlex.quote(str(target_file))}\n"
        "fi\n"
        f"exec {shlex.quote(real_sha256sum)} \"$@\"\n"
    )
    shim_script.chmod(0o755)

    result = _run_archive(data_dir, archive_root, shim_dir, path_prefix=str(mutate_shim_dir))

    assert result.returncode != 0
    assert target_file.read_bytes() != original_bytes
    assert list(archive_root.iterdir()) == []
    assert list(archive_root.glob(".player-games-archive.*")) == []


def test_archive_snapshot_detects_coherent_historical_mutation_during_copy(
    snapshot_fixture, tmp_path,
):
    """Would fail if the post-copy live revalidation relied only on the
    publication-manifest digest plus structural raw validation: a
    quarantined-season body mutated together with a coherently rewritten
    sidecar passes both, and only a byte comparison of the live trees
    against the just-checksummed stage can catch it."""
    data_dir, _reference_dir, _manifest_sha256 = _materialize_data_dir(snapshot_fixture)
    shim_dir = _write_season_scope_shim(tmp_path)
    archive_root = tmp_path / "archive_root"
    archive_root.mkdir()

    target_body = data_dir / "raw/stats_nba/player_logs/season=1984/regular.json"
    original_bytes = target_body.read_bytes()
    mutate_py = tmp_path / "mutate_1984_coherently.py"
    mutate_py.write_text(
        "import hashlib, json\n"
        "from pathlib import Path\n"
        f"body = Path({str(target_body)!r})\n"
        "sidecar = body.with_name(body.name + '.meta.json')\n"
        "payload = json.loads(body.read_text(encoding='utf-8'))\n"
        "payload['resultSets'][0]['rowSet'][0][2] = 'Coherently Mutated'\n"
        "data = json.dumps(payload).encode('utf-8')\n"
        "body.write_bytes(data)\n"
        "meta = json.loads(sidecar.read_text(encoding='utf-8'))\n"
        "meta['sha256'] = hashlib.sha256(data).hexdigest()\n"
        "meta['content_length'] = len(data)\n"
        "sidecar.write_text(json.dumps(meta), encoding='utf-8')\n"
    )

    mutate_shim_dir = tmp_path / "mutate_shim"
    mutate_shim_dir.mkdir()
    real_sha256sum = shutil.which("sha256sum")
    venv_python = REPO_ROOT / ".venv/bin/python"
    shim_script = mutate_shim_dir / "sha256sum"
    shim_script.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$#\" -eq 3 ] && [ \"$1\" = \"--check\" ] && [ \"$2\" = \"--strict\" ] "
        "&& [ \"$3\" = \"SHA256SUMS\" ]; then\n"
        f"  {shlex.quote(str(venv_python))} {shlex.quote(str(mutate_py))}\n"
        "fi\n"
        f"exec {shlex.quote(real_sha256sum)} \"$@\"\n"
    )
    shim_script.chmod(0o755)

    result = _run_archive(data_dir, archive_root, shim_dir, path_prefix=str(mutate_shim_dir))

    assert result.returncode != 0
    assert "changed during archive copy" in result.stderr
    assert target_body.read_bytes() != original_bytes
    assert list(archive_root.iterdir()) == []


def test_archive_snapshot_cleanup_preserves_swapped_stage(snapshot_fixture, tmp_path):
    """Would fail if the EXIT cleanup deleted whatever currently occupies
    the staging path instead of only the exact directory (by device/inode)
    this run's own `mktemp` created."""
    data_dir, _reference_dir, _manifest_sha256 = _materialize_data_dir(snapshot_fixture)
    shim_dir = _write_season_scope_shim(tmp_path)
    archive_root = tmp_path / "archive_root"
    archive_root.mkdir()
    aside = tmp_path / "stage_moved_aside"

    # Swap the owned stage for a foreign directory exactly while the stage's
    # own SHA256SUMS check runs; the manifest read right after then fails
    # (the replacement is empty), forcing the exit path whose cleanup must
    # leave the unowned replacement untouched.
    swap_shim_dir = tmp_path / "swap_shim"
    swap_shim_dir.mkdir()
    real_sha256sum = shutil.which("sha256sum")
    shim_script = swap_shim_dir / "sha256sum"
    shim_script.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$#\" -eq 3 ] && [ \"$1\" = \"--check\" ] && [ \"$2\" = \"--strict\" ] "
        "&& [ \"$3\" = \"SHA256SUMS\" ]; then\n"
        f"  stage=\"$(find {shlex.quote(str(archive_root))} -mindepth 1 -maxdepth 1 "
        "-name '.player-games-archive.*' -type d -print -quit)\"\n"
        "  if [ -n \"$stage\" ]; then\n"
        f"    mv \"$stage\" {shlex.quote(str(aside))}\n"
        "    mkdir \"$stage\"\n"
        "    printf 'not mine\\n' > \"$stage/sentinel.txt\"\n"
        "  fi\n"
        "fi\n"
        f"exec {shlex.quote(real_sha256sum)} \"$@\"\n"
    )
    shim_script.chmod(0o755)

    result = _run_archive(data_dir, archive_root, shim_dir, path_prefix=str(swap_shim_dir))

    assert result.returncode != 0
    replacements = list(archive_root.glob(".player-games-archive.*"))
    assert len(replacements) == 1
    assert (replacements[0] / "sentinel.txt").read_text() == "not mine\n"
    assert aside.is_dir()


def test_archive_snapshot_rejects_symlinked_existing_snapshot_wrapper(
    archived_snapshot, tmp_path,
):
    """Would fail if the idempotency path's layout check only rejected
    symlinks at the leaf components: a symlinked `raw` wrapper is followed
    by every byte-level comparison while the lstat-visible inventory covers
    none of it."""
    snapshot_dir = archived_snapshot.archive_root / archived_snapshot.name
    outside_raw = tmp_path / "outside_raw"
    shutil.move(str(snapshot_dir / "raw"), str(outside_raw))
    (snapshot_dir / "raw").symlink_to(outside_raw)

    result = _run_archive(
        archived_snapshot.data_dir,
        archived_snapshot.archive_root,
        archived_snapshot.shim_dir,
    )

    assert result.returncode != 0
    assert "unsafe" in result.stderr
    assert (snapshot_dir / "raw").is_symlink()


def test_archive_snapshot_signal_leaves_no_final(snapshot_fixture, tmp_path):
    """A signal trap that doesn't itself call exit lets bash resume after
    running it -- send SIGTERM once staging exists and is blocked in
    (shimmed) rsync, and require the script to actually terminate nonzero
    with staging cleaned up, not silently finish the archive and exit 0."""
    data_dir, _reference_dir, _manifest_sha256 = _materialize_data_dir(snapshot_fixture)
    shim_dir = _write_season_scope_shim(tmp_path)
    archive_root = tmp_path / "archive_root"
    archive_root.mkdir()

    rsync_shim_dir = tmp_path / "rsync_shim"
    rsync_shim_dir.mkdir()
    rsync_script = rsync_shim_dir / "rsync"
    rsync_script.write_text("#!/usr/bin/env bash\nsleep 2\n")
    rsync_script.chmod(0o755)

    default_capacity = _default_capacity_shim(tmp_path)
    env = dict(os.environ)
    env["NBA_PLAYER_GAMES_DATA_DIR"] = str(data_dir)
    env["NBA_PLAYER_GAMES_ARCHIVE_ROOT"] = str(archive_root)
    env["PYTHONPATH"] = str(shim_dir)
    env["PATH"] = f"{rsync_shim_dir}:{default_capacity}:{env.get('PATH', '')}"

    proc = subprocess.Popen(
        [str(ARCHIVE_SCRIPT)], env=env, cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 10
        staging_dirs = []
        while time.monotonic() < deadline:
            staging_dirs = list(archive_root.glob(".player-games-archive.*"))
            if staging_dirs:
                break
            time.sleep(0.05)
        assert staging_dirs, "staging directory never appeared"

        proc.send_signal(signal.SIGTERM)
        _stdout, _stderr = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise

    assert proc.returncode is not None and proc.returncode != 0
    assert list(archive_root.glob(".player-games-archive.*")) == []
    assert list(archive_root.iterdir()) == []


# ---------------------------------------------------------------------------
# Restore behaviors
# ---------------------------------------------------------------------------


def test_restore_snapshot_places_raw_and_publication_after_rebuild(
    archived_snapshot,
    tmp_path,
):
    restore_root = tmp_path / "restore"
    result = _run_restore_script(
        archived_snapshot,
        restore_root,
    )

    assert result.returncode == 0, result.stderr
    assert (restore_root / "raw/stats_nba/player_logs").is_dir()
    assert (
        restore_root / "parquet-large/player_games/_manifest.json"
    ).is_file()
    assert "independent rebuild: ok" in result.stdout


@pytest.mark.parametrize("which", ["raw", "publication"])
def test_restore_snapshot_refuses_when_either_target_exists(
    archived_snapshot, tmp_path, which,
):
    restore_root = tmp_path / "restore"
    restore_root.mkdir()
    raw_target = restore_root / "raw/stats_nba/player_logs"
    publication_target = restore_root / "parquet-large/player_games"
    target = raw_target if which == "raw" else publication_target
    other = publication_target if which == "raw" else raw_target
    target.mkdir(parents=True)
    (target / "sentinel.txt").write_text("pre-existing\n")

    result = _run_restore_script(archived_snapshot, restore_root)

    assert result.returncode != 0
    assert (target / "sentinel.txt").read_text() == "pre-existing\n"
    assert not other.exists()


@pytest.mark.parametrize("which", ["raw", "publication"])
def test_restore_snapshot_refuses_symlink_targets(archived_snapshot, tmp_path, which):
    restore_root = tmp_path / "restore"
    restore_root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    raw_target = restore_root / "raw/stats_nba/player_logs"
    publication_target = restore_root / "parquet-large/player_games"
    target = raw_target if which == "raw" else publication_target
    other = publication_target if which == "raw" else raw_target
    target.parent.mkdir(parents=True)
    target.symlink_to(elsewhere)

    result = _run_restore_script(archived_snapshot, restore_root)

    assert result.returncode != 0
    assert target.is_symlink()
    assert os.readlink(target) == str(elsewhere)
    assert not other.exists()


def test_restore_snapshot_detects_snapshot_mutation(archived_snapshot, tmp_path):
    """Would fail if restore trusted the archived snapshot without
    revalidating its SHA256SUMS/layout for itself before copying from it."""
    restore_root = tmp_path / "restore"
    snapshot_dir = archived_snapshot.archive_root / archived_snapshot.name
    sums_path = snapshot_dir / "SHA256SUMS"
    original_text = sums_path.read_text()
    lines = original_text.splitlines()
    digest, rest = lines[0].split("  ", 1)
    tampered_digest = ("0" if digest[0] != "0" else "1") + digest[1:]
    lines[0] = f"{tampered_digest}  {rest}"

    try:
        sums_path.write_text("\n".join(lines) + "\n")

        result = _run_restore_script(archived_snapshot, restore_root)

        assert result.returncode != 0
        assert not (restore_root / "raw/stats_nba/player_logs").exists()
        assert not (restore_root / "parquet-large/player_games").exists()
    finally:
        sums_path.write_text(original_text)


def test_restore_snapshot_rejects_rebuild_difference(archived_snapshot, tmp_path):
    """Corrupts the *staged* raw copy -- not the archived snapshot -- right
    between the post-copy snapshot revalidation and the independent
    verify-rebuild call, proving verify-rebuild is a real, distinct check
    on the staged bytes rather than a restatement of the snapshot check."""
    restore_root = tmp_path / "restore"
    restore_root.mkdir()

    mutate_shim_dir = tmp_path / "mutate_shim"
    mutate_shim_dir.mkdir()
    real_sha256sum = shutil.which("sha256sum")
    counter_file = tmp_path / "sha256sum_call_count"
    shim_script = mutate_shim_dir / "sha256sum"
    shim_script.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$#\" -eq 3 ] && [ \"$1\" = \"--check\" ] && [ \"$2\" = \"--strict\" ] "
        "&& [ \"$3\" = \"SHA256SUMS\" ]; then\n"
        f"  count=0\n"
        f"  [ -f {shlex.quote(str(counter_file))} ] && "
        f"count=\"$(cat {shlex.quote(str(counter_file))})\"\n"
        "  count=$((count + 1))\n"
        f"  echo \"$count\" > {shlex.quote(str(counter_file))}\n"
        "  if [ \"$count\" -eq 2 ]; then\n"
        f"    stage=\"$(find {shlex.quote(str(restore_root))} "
        "-name '.player-games-restore-raw.*' -type d | head -n1)\"\n"
        "    if [ -n \"$stage\" ]; then\n"
        "      printf '{\"resultSets\": []}' > \"$stage/season=2012/regular.json\"\n"
        "    fi\n"
        "  fi\n"
        "fi\n"
        f"exec {shlex.quote(real_sha256sum)} \"$@\"\n"
    )
    shim_script.chmod(0o755)

    result = _run_restore_script(
        archived_snapshot, restore_root, path_prefix=str(mutate_shim_dir),
    )

    assert result.returncode != 0
    assert not (restore_root / "raw/stats_nba/player_logs").exists()
    assert not (restore_root / "parquet-large/player_games").exists()


def test_restore_snapshot_rolls_back_partial_placement(archived_snapshot, tmp_path):
    restore_root = tmp_path / "restore"
    restore_root.mkdir()
    raw_destination = restore_root / "raw/stats_nba/player_logs"
    publication_destination = restore_root / "parquet-large/player_games"
    raw_parent = raw_destination.parent
    publication_parent = publication_destination.parent
    raw_parent.mkdir(parents=True)
    publication_parent.mkdir(parents=True)

    # Locking `publication_parent` must happen strictly *after* the first
    # (`raw_stage` -> `raw_destination`) rename -- everything up through
    # `verify-rebuild` (which itself stages a temp rebuild directory next
    # to the staged publication, i.e. inside `publication_parent`) must
    # still be able to run normally. Shimming `mv` to lock right after it
    # performs exactly that first rename lands the lock precisely between
    # the two renames, rather than anywhere earlier in the script.
    shim_dir = tmp_path / "lock_shim"
    shim_dir.mkdir()
    real_mv = shutil.which("mv")
    mv_script = shim_dir / "mv"
    mv_script.write_text(
        "#!/usr/bin/env bash\n"
        f"{shlex.quote(real_mv)} \"$@\"\n"
        "rc=$?\n"
        "for arg in \"$@\"; do\n"
        f"  if [ \"$arg\" = {shlex.quote(str(raw_destination))} ]; then\n"
        f"    chmod 555 -- {shlex.quote(str(publication_parent))}\n"
        "  fi\n"
        "done\n"
        "exit \"$rc\"\n"
    )
    mv_script.chmod(0o755)

    try:
        result = _run_restore_script(
            archived_snapshot, restore_root, path_prefix=str(shim_dir),
        )
        assert result.returncode != 0
        assert "rolled back" in result.stderr.lower()
        assert not raw_destination.exists()
        assert not publication_destination.exists()
        assert list(raw_parent.glob(".player-games-restore-raw.*")) == []
    finally:
        publication_parent.chmod(0o755)


def test_restore_snapshot_rejects_symlinked_raw_wrapper(archived_snapshot, tmp_path):
    """Would fail if the snapshot layout check only rejected symlinks at the
    leaf components: a symlinked `raw` wrapper is followed by `test -d`,
    rsync, and validate-layout, while `find`'s lstat inventory sees no raw
    files at all -- so a SHA256SUMS covering zero raw files still passes the
    inventory-equality check and the restore trusts unchecksummed bytes."""
    tampered_root = tmp_path / "tampered_root"
    tampered_root.mkdir()
    snapshot_dir = archived_snapshot.archive_root / archived_snapshot.name
    tampered = tampered_root / archived_snapshot.name
    tampered.mkdir()
    shutil.copytree(snapshot_dir / "parquet-large", tampered / "parquet-large")
    outside_raw = tmp_path / "outside_raw"
    shutil.copytree(snapshot_dir / "raw", outside_raw)
    (tampered / "raw").symlink_to(outside_raw)
    sums_lines = []
    for path in sorted((tampered / "parquet-large").rglob("*")):
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        sums_lines.append(f"{digest}  {path.relative_to(tampered)}")
    (tampered / "SHA256SUMS").write_text("\n".join(sums_lines) + "\n")

    restore_root = tmp_path / "restore"
    result = _run_restore_script(
        archived_snapshot,
        restore_root,
        extra_env={"NBA_PLAYER_GAMES_ARCHIVE_ROOT": str(tampered_root)},
    )

    assert result.returncode != 0
    assert "unsafe" in result.stderr
    assert not (restore_root / "raw/stats_nba/player_logs").exists()
    assert not (restore_root / "parquet-large/player_games").exists()


def test_restore_snapshot_sigterm_between_renames_rolls_back(archived_snapshot, tmp_path):
    """Would fail if a signal landing between the two publish renames only
    ran the owned-stage cleanup: the raw destination would stay published
    while the publication stage was deleted -- the exact half-published
    state the rollback state machine exists to prevent. The `du` integrity
    probe runs precisely between the renames, so a shimmed `du` that
    SIGTERMs the script parent lands the signal deterministically inside
    the window."""
    restore_root = tmp_path / "restore"
    raw_destination = restore_root / "raw/stats_nba/player_logs"
    publication_destination = restore_root / "parquet-large/player_games"

    shim_dir = tmp_path / "term_shim"
    shim_dir.mkdir()
    real_du = shutil.which("du")
    du_script = shim_dir / "du"
    du_script.write_text(
        "#!/usr/bin/env bash\n"
        "kill -TERM \"$PPID\"\n"
        f"exec {shlex.quote(real_du)} \"$@\"\n"
    )
    du_script.chmod(0o755)

    result = _run_restore_script(
        archived_snapshot, restore_root, path_prefix=str(shim_dir),
    )

    assert result.returncode != 0
    assert "rolled back" in result.stderr.lower()
    assert not raw_destination.exists()
    assert not publication_destination.exists()
    assert list(raw_destination.parent.glob(".player-games-restore-raw.*")) == []
    assert list(
        publication_destination.parent.glob(".player-games-restore-publication.*")
    ) == []


def test_restore_snapshot_preserves_preexisting_unowned_stages(archived_snapshot, tmp_path):
    """Would fail if cleanup swept every path merely matching the staging
    name pattern instead of removing only the exact directory this run's
    own `mktemp` created (matched by device/inode identity)."""
    restore_root = tmp_path / "restore"
    raw_parent = restore_root / "raw/stats_nba"
    raw_parent.mkdir(parents=True)
    unowned = raw_parent / ".player-games-restore-raw.PREEXISTING"
    unowned.mkdir()
    (unowned / "leftover.txt").write_text("not mine\n")

    result = _run_restore_script(archived_snapshot, restore_root)

    assert result.returncode == 0, result.stderr
    assert unowned.is_dir()
    assert (unowned / "leftover.txt").read_text() == "not mine\n"
