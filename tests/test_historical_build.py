import hashlib
import json
import os
from pathlib import Path

import pandas as pd
import pytest

from nba_data import cache, historical_bref, historical_build


SEED_INDEX_HTML = (
    b'<html><body>'
    b'<a href="/leagues/BAA_1947_games-november.html">November</a>'
    b"</body></html>"
)
SEED_MONTH_FIXTURE = (
    Path(__file__).parent
    / "fixtures/historical_bref/BAA_1947_games-november.html"
)
SEED_MONTH_URL = (
    "https://www.basketball-reference.com/leagues/"
    "BAA_1947_games-november.html"
)


COUNT_COLUMNS = [
    "season", "league", "expected_games", "source_url", "source_note"
]
CORRECTION_COLUMNS = ["game_id", "site_status", "source_url", "note"]


def write_counts(tmp_path, rows):
    path = tmp_path / "counts.csv"
    pd.DataFrame(rows, columns=COUNT_COLUMNS).to_csv(path, index=False)
    return path


def valid_count_rows():
    return [
        {
            "season": season,
            "league": "BAA" if season <= 1949 else "NBA",
            "expected_games": 1,
            "source_url": f"https://example.org/{season}",
            "source_note": "Independent summary total.",
        }
        for season in range(1947, 1984)
    ]


def write_corrections(tmp_path, rows):
    path = tmp_path / "corrections.csv"
    pd.DataFrame(rows, columns=CORRECTION_COLUMNS).to_csv(path, index=False)
    return path


def test_expected_counts_cover_exact_historical_seasons():
    counts = historical_build.load_expected_counts(
        Path("data/reference/historical_season_counts.csv")
    )
    assert tuple(counts) == tuple(range(1947, 1984))
    assert counts[1947] == 331
    assert counts[1960] == 300
    assert counts[1983] == 943


def test_site_correction_changes_only_status():
    games = pd.DataFrame([
        {
            "game_id": "bref-before",
            "site_status": "home",
            "home_pts": 99,
            "away_pts": 88,
            "attendance": 1000,
        },
        {
            "game_id": "bref-194611010TRH",
            "site_status": "unknown",
            "home_pts": 66,
            "away_pts": 68,
            "attendance": 4329,
        },
        {
            "game_id": "bref-after",
            "site_status": "neutral",
            "home_pts": 77,
            "away_pts": 76,
            "attendance": pd.NA,
        },
    ])
    corrections = pd.DataFrame([{
        "game_id": "bref-194611010TRH",
        "site_status": "neutral",
        "source_url": "https://example.org/documented-neutral-game",
        "note": "Contemporary source identifies a neutral venue.",
    }])

    result = historical_build.apply_site_corrections(games, corrections)

    expected = games.copy()
    expected.loc[1, "site_status"] = "neutral"
    pd.testing.assert_frame_equal(result, expected)


def test_counts_reject_missing_ending_season(tmp_path):
    rows = valid_count_rows()
    rows.pop()

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="historical counts must cover exactly 1947..1983",
    ):
        historical_build.load_expected_counts(write_counts(tmp_path, rows))


def test_counts_reject_duplicate_season(tmp_path):
    rows = valid_count_rows()
    rows[-1]["season"] = 1982

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="duplicate historical count season",
    ):
        historical_build.load_expected_counts(write_counts(tmp_path, rows))


def test_counts_reject_wrong_league(tmp_path):
    rows = valid_count_rows()
    rows[0]["league"] = "NBA"

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="historical count league/season mismatch",
    ):
        historical_build.load_expected_counts(write_counts(tmp_path, rows))


def test_counts_reject_blank_provenance(tmp_path):
    rows = valid_count_rows()
    rows[0]["source_note"] = " "

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="historical counts require source URL and note",
    ):
        historical_build.load_expected_counts(write_counts(tmp_path, rows))


def test_corrections_reject_duplicate_game_id(tmp_path):
    rows = [
        {
            "game_id": "bref-one",
            "site_status": "home",
            "source_url": "https://example.org/one",
            "note": "Documented home venue.",
        },
        {
            "game_id": "bref-one",
            "site_status": "neutral",
            "source_url": "https://example.org/two",
            "note": "Documented neutral venue.",
        },
    ]

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="duplicate historical site correction game_id",
    ):
        historical_build.load_site_corrections(write_corrections(tmp_path, rows))


def test_corrections_reject_unknown_as_selected_value(tmp_path):
    rows = [{
        "game_id": "bref-one",
        "site_status": "unknown",
        "source_url": "https://example.org/one",
        "note": "Not an allowed selected value.",
    }]

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="site correction must be home or neutral",
    ):
        historical_build.load_site_corrections(write_corrections(tmp_path, rows))


@pytest.mark.parametrize("status", [None, " "])
def test_corrections_reject_missing_or_blank_selected_status(tmp_path, status):
    rows = [{
        "game_id": "bref-one",
        "site_status": status,
        "source_url": "https://example.org/one",
        "note": "A correction needs a selected status.",
    }]

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="site correction must be home or neutral",
    ):
        historical_build.load_site_corrections(write_corrections(tmp_path, rows))


@pytest.mark.parametrize("column", ["source_url", "note"])
def test_corrections_reject_blank_source_or_note(tmp_path, column):
    rows = [{
        "game_id": "bref-one",
        "site_status": "home",
        "source_url": "https://example.org/one",
        "note": "Documented home venue.",
    }]
    rows[0][column] = " "

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match=f"site correction requires {column}",
    ):
        historical_build.load_site_corrections(write_corrections(tmp_path, rows))


def test_apply_corrections_rejects_orphan_game_id():
    games = pd.DataFrame([{"game_id": "bref-one", "site_status": "unknown"}])
    corrections = pd.DataFrame([{
        "game_id": "bref-missing",
        "site_status": "neutral",
        "source_url": "https://example.org/missing",
        "note": "Documented neutral venue.",
    }])

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match=r"site correction must resolve exactly one game: \['bref-missing'\]",
    ):
        historical_build.apply_site_corrections(games, corrections)


def test_apply_corrections_rejects_duplicate_game_id_target():
    games = pd.DataFrame([
        {"game_id": "bref-duplicate", "site_status": "unknown"},
        {"game_id": "bref-duplicate", "site_status": "unknown"},
    ])
    corrections = pd.DataFrame([{
        "game_id": "bref-duplicate",
        "site_status": "neutral",
        "source_url": "https://example.org/duplicate",
        "note": "Documented neutral venue.",
    }])

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match=r"site correction must resolve exactly one game: \['bref-duplicate'\]",
    ):
        historical_build.apply_site_corrections(games, corrections)


def test_empty_correction_ledger_loads(tmp_path):
    path = tmp_path / "corrections.csv"
    path.write_text(",".join(CORRECTION_COLUMNS) + "\n")

    corrections = historical_build.load_site_corrections(path)

    assert corrections.empty
    assert corrections.columns.tolist() == CORRECTION_COLUMNS


def source_game(
    *,
    game_id="bref-194611010TRH",
    season=1947,
    league="BAA",
    away="New York Knicks",
    home="Toronto Huskies",
    away_pts=68,
    home_pts=66,
    overtime_marker="",
    overtime_periods=0,
    site_status="home",
):
    return {
        "game_id": game_id,
        "season": season,
        "league": league,
        "game_date": pd.Timestamp("1946-11-01").date(),
        "away_team_name": away,
        "home_team_name": home,
        "away_pts": away_pts,
        "home_pts": home_pts,
        "overtime_marker": overtime_marker,
        "overtime_periods": overtime_periods,
        "site_status": site_status,
        "venue_name": pd.NA,
        "attendance": pd.NA,
        "attendance_status": "missing",
        "boxscore_url": (
            "https://www.basketball-reference.com/boxscores/"
            "194611010TRH.html"
        ),
        "schedule_url": (
            "https://www.basketball-reference.com/leagues/"
            "BAA_1947_games-november.html"
        ),
        "game_remarks": "",
    }


def seed_historical_references(data_dir, counts):
    reference = data_dir / "reference"
    reference.mkdir(parents=True, exist_ok=True)
    lines = ["season,league,expected_games,source_url,source_note"]
    for season, games in sorted(counts.items()):
        league = historical_build.historical_bref.league_for_season(season)
        lines.append(
            f"{season},{league},{games},"
            "https://www.basketball-reference.com/leagues/"
            f"{league}_{season}.html,Curated test count."
        )
    (reference / "historical_season_counts.csv").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    (reference / "historical_site_corrections.csv").write_text(
        "game_id,site_status,source_url,note\n", encoding="utf-8"
    )


def assembled_game_frame():
    frame = pd.DataFrame([{
        **source_game(),
        "away_franchise_id": "NYK",
        "home_franchise_id": "TOR_1946",
        "home_margin": -2,
    }])
    frame = frame.loc[:, historical_build.HISTORICAL_COLUMNS]
    return frame.astype(historical_build.HISTORICAL_DTYPES)


def seed_verified_historical_cache(repo_dir):
    cache_dir = repo_dir / "raw/historical_bref"
    cache.publish_bytes(
        cache_dir / "BAA_1947_games.html",
        SEED_INDEX_HTML,
        {
            "provider": "basketball_reference",
            "resource": "historical_schedule_index",
            "league": "BAA",
            "season": 1947,
            "url": historical_bref.index_url(1947),
        },
        lambda path: historical_bref.validate_index(path, 1947),
    )
    cache.publish_bytes(
        cache_dir / "BAA_1947_games-november.html",
        SEED_MONTH_FIXTURE.read_bytes(),
        {
            "provider": "basketball_reference",
            "resource": "historical_schedule_month",
            "league": "BAA",
            "season": 1947,
            "month": "november",
            "url": SEED_MONTH_URL,
        },
        lambda path: historical_bref.validate_month(
            path, 1947, SEED_MONTH_URL
        ),
    )


def test_raw_provenance_rejects_orphan_body(tmp_path):
    """Would fail if a lone historical body could enter a manifest."""
    cache_dir = tmp_path / "raw/historical_bref"
    cache_dir.mkdir(parents=True)
    (cache_dir / "BAA_1947_games.html").write_bytes(b"<html></html>")

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="incomplete historical cache pair",
    ):
        historical_build._raw_provenance(tmp_path)


def test_raw_provenance_rejects_metadata_url_mismatch(tmp_path):
    """Would fail if a verified body could be claimed by another URL."""
    seed_verified_historical_cache(tmp_path)
    sidecar = next(
        (tmp_path / "raw/historical_bref").glob(
            "*games-november.html.meta.json"
        )
    )
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    metadata["url"] = "https://example.org/wrong"
    sidecar.write_text(json.dumps(metadata, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="historical cache metadata mismatch",
    ):
        historical_build._raw_provenance(tmp_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("resource", "wrong", id="resource"),
        pytest.param("league", "NBA", id="league"),
        pytest.param("season", 1983, id="season"),
        pytest.param("month", "december", id="month"),
    ],
)
def test_raw_provenance_rejects_each_month_sidecar_identity_field(
    tmp_path, field, value
):
    """Would fail if a historical month sidecar could claim another identity."""
    seed_verified_historical_cache(tmp_path)
    sidecar = (
        tmp_path / "raw/historical_bref/"
        "BAA_1947_games-november.html.meta.json"
    )
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    metadata[field] = value
    sidecar.write_text(json.dumps(metadata) + "\n", encoding="utf-8")

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="historical cache metadata mismatch",
    ):
        historical_build._raw_provenance(tmp_path)


@pytest.mark.parametrize("duplicate", ["identity", "url"])
def test_raw_provenance_rejects_duplicate_month_metadata(tmp_path, duplicate):
    """Would fail if two discovered pages shared sidecar identity or URL."""
    seed_verified_historical_cache(tmp_path)
    cache_dir = tmp_path / "raw/historical_bref"
    index = cache_dir / "BAA_1947_games.html"
    index.write_bytes(
        SEED_INDEX_HTML.replace(
            b"</body>",
            b'<a href="/leagues/BAA_1947_games-december.html">December</a></body>',
        )
    )
    index_meta = json.loads(
        index.with_name(index.name + ".meta.json").read_text(encoding="utf-8")
    )
    index_meta["sha256"] = hashlib.sha256(index.read_bytes()).hexdigest()
    index.with_name(index.name + ".meta.json").write_text(
        json.dumps(index_meta) + "\n", encoding="utf-8"
    )
    december = cache_dir / "BAA_1947_games-december.html"
    cache.publish_bytes(
        december,
        SEED_MONTH_FIXTURE.read_bytes(),
        {
            "provider": "basketball_reference",
            "resource": "historical_schedule_month",
            "league": "BAA",
            "season": 1947,
            "month": "december",
            "url": (
                "https://www.basketball-reference.com/leagues/"
                "BAA_1947_games-december.html"
            ),
        },
        lambda path: historical_bref.validate_month(
            path,
            1947,
            "https://www.basketball-reference.com/leagues/"
            "BAA_1947_games-december.html",
        ),
    )
    sidecar = cache_dir / "BAA_1947_games-november.html.meta.json"
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    if duplicate == "identity":
        metadata["month"] = "december"
    else:
        metadata["url"] = (
            "https://www.basketball-reference.com/leagues/"
            "BAA_1947_games-december.html"
        )
    sidecar.write_text(json.dumps(metadata) + "\n", encoding="utf-8")

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="duplicate historical cache identity",
    ):
        historical_build._raw_provenance(tmp_path)


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param("orphan_sidecar", id="orphan-sidecar"),
        pytest.param("checksum", id="checksum"),
        pytest.param("identity", id="identity"),
        pytest.param("extra", id="unexpected-file"),
        pytest.param("months", id="discovered-month-set"),
    ],
)
def test_raw_provenance_rejects_unattestable_cache_inventory(tmp_path, mutation):
    """Would fail if cache inventory drift were silently attested."""
    seed_verified_historical_cache(tmp_path)
    cache_dir = tmp_path / "raw/historical_bref"
    if mutation == "orphan_sidecar":
        (cache_dir / "BAA_1947_games-december.html.meta.json").write_text(
            "{}\n", encoding="utf-8"
        )
    elif mutation == "checksum":
        (cache_dir / "BAA_1947_games-november.html").write_bytes(b"changed")
    elif mutation == "identity":
        sidecar = cache_dir / "BAA_1947_games.html.meta.json"
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        metadata["provider"] = "wrong"
        sidecar.write_text(json.dumps(metadata) + "\n", encoding="utf-8")
    elif mutation == "extra":
        (cache_dir / "notes.txt").write_text("not cache", encoding="utf-8")
    else:
        (cache_dir / "BAA_1947_games.html").write_bytes(
            SEED_INDEX_HTML.replace(b"</body>", b'<a href="/leagues/BAA_1947_games-december.html">December</a></body>')
        )
        sidecar = cache_dir / "BAA_1947_games.html.meta.json"
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        metadata["sha256"] = hashlib.sha256(
            (cache_dir / "BAA_1947_games.html").read_bytes()
        ).hexdigest()
        sidecar.write_text(json.dumps(metadata) + "\n", encoding="utf-8")

    with pytest.raises(historical_build.HistoricalValidationError):
        historical_build._raw_provenance(tmp_path)


def test_publish_historical_pair_restores_complete_pair_after_replace_failure(
    monkeypatch, tmp_path
):
    """Would fail if a second historical replacement lost the first old file."""
    output = tmp_path / "parquet"
    stage = tmp_path / "stage"
    output.mkdir()
    stage.mkdir()
    old = {
        "historical_games.parquet": b"old games",
        "historical_games_manifest.json": b"old manifest",
    }
    for name, payload in old.items():
        (output / name).write_bytes(payload)
        (stage / name).write_bytes(b"new " + payload)
    actual_replace = os.replace
    moves = 0

    def fail_second_stage_move(source, destination):
        nonlocal moves
        if Path(source).parent == stage and Path(destination).parent == output:
            moves += 1
            if moves == 2:
                raise OSError("seeded replacement failure")
        actual_replace(source, destination)

    monkeypatch.setattr(historical_build.os, "replace", fail_second_stage_move)

    with pytest.raises(OSError, match="seeded replacement failure"):
        historical_build._publish_historical_pair(stage, output)

    assert {name: (output / name).read_bytes() for name in old} == old


def test_publish_historical_pair_restores_empty_output_after_second_move_failure(
    monkeypatch, tmp_path
):
    """Would fail if a first publication left a one-file product after failure."""
    output = tmp_path / "parquet"
    stage = tmp_path / "stage"
    output.mkdir()
    stage.mkdir()
    for name in historical_build.HISTORICAL_PRODUCTS:
        (stage / name).write_bytes(name.encode())
    real_replace = os.replace
    moves = 0

    def fail_second_publication_move(source, destination):
        nonlocal moves
        if Path(source).parent == stage and Path(destination).parent == output:
            moves += 1
            if moves == 2:
                raise OSError("seeded publication failure")
        real_replace(source, destination)

    monkeypatch.setattr(
        historical_build.os, "replace", fail_second_publication_move
    )

    with pytest.raises(OSError, match="seeded publication failure"):
        historical_build._publish_historical_pair(stage, output)

    assert not any((output / name).exists() for name in historical_build.HISTORICAL_PRODUCTS)
    assert list(tmp_path.glob(".historical-rollback-*")) == []


def test_publish_historical_pair_restores_prior_after_second_backup_failure(
    monkeypatch, tmp_path
):
    """Would fail if backing up the second old product split a prior pair."""
    output = tmp_path / "parquet"
    stage = tmp_path / "stage"
    output.mkdir()
    stage.mkdir()
    old = {}
    for name in historical_build.HISTORICAL_PRODUCTS:
        old[name] = b"old " + name.encode()
        (output / name).write_bytes(old[name])
        (stage / name).write_bytes(b"new " + name.encode())
    real_replace = os.replace
    backups = 0

    def fail_second_backup_move(source, destination):
        nonlocal backups
        if Path(source).parent == output and Path(destination).parent.name.startswith(
            ".historical-rollback-"
        ):
            backups += 1
            if backups == 2:
                raise OSError("seeded backup failure")
        real_replace(source, destination)

    monkeypatch.setattr(historical_build.os, "replace", fail_second_backup_move)

    with pytest.raises(OSError, match="seeded backup failure"):
        historical_build._publish_historical_pair(stage, output)

    assert {name: (output / name).read_bytes() for name in old} == old
    assert list(tmp_path.glob(".historical-rollback-*")) == []


def test_publish_historical_pair_retains_recovery_after_restoration_failure(
    monkeypatch, tmp_path
):
    """Would fail if a failed restoration destroyed the only prior pair."""
    output = tmp_path / "parquet"
    stage = tmp_path / "stage"
    output.mkdir()
    stage.mkdir()
    old = {}
    for name in historical_build.HISTORICAL_PRODUCTS:
        old[name] = b"old " + name.encode()
        (output / name).write_bytes(old[name])
        (stage / name).write_bytes(b"new " + name.encode())
    real_replace = os.replace
    publication_moves = 0

    def fail_publication_then_restoration(source, destination):
        nonlocal publication_moves
        source = Path(source)
        destination = Path(destination)
        if source.parent == stage and destination.parent == output:
            publication_moves += 1
            if publication_moves == 2:
                raise OSError("seeded publication failure")
        if (
            source.parent.name.startswith(
                (".historical-rollback-", ".historical-restoration-")
            )
            and destination.parent == output
        ):
            raise OSError("seeded restoration failure")
        real_replace(source, destination)

    monkeypatch.setattr(
        historical_build.os, "replace", fail_publication_then_restoration
    )

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="historical publication restoration failed",
    ) as caught:
        historical_build._publish_historical_pair(stage, output)

    recovery_dirs = list(tmp_path.glob(".historical-rollback-*"))
    assert len(recovery_dirs) == 1
    recovery_dir = recovery_dirs[0]
    assert str(recovery_dir) in str(caught.value)
    assert {
        name: (recovery_dir / name).read_bytes() for name in old
    } == old


def test_publish_retains_complete_pair_after_second_restoration_failure(
    monkeypatch, tmp_path
):
    """Would fail if partial restoration split the prior recovery pair."""
    output = tmp_path / "parquet"
    stage = tmp_path / "stage"
    output.mkdir()
    stage.mkdir()
    old = {}
    for name in historical_build.HISTORICAL_PRODUCTS:
        old[name] = b"old " + name.encode()
        (output / name).write_bytes(old[name])
        (stage / name).write_bytes(b"new " + name.encode())
    real_replace = os.replace
    publication_moves = 0
    restoration_moves = 0

    def fail_publication_then_second_restoration(source, destination):
        nonlocal publication_moves, restoration_moves
        source = Path(source)
        destination = Path(destination)
        if source.parent == stage and destination.parent == output:
            publication_moves += 1
            if publication_moves == 2:
                raise OSError("seeded publication failure")
        if (
            source.parent.name.startswith(
                (".historical-rollback-", ".historical-restoration-")
            )
            and destination.parent == output
        ):
            restoration_moves += 1
            if restoration_moves == 2:
                raise OSError("seeded second restoration failure")
        real_replace(source, destination)

    monkeypatch.setattr(
        historical_build.os,
        "replace",
        fail_publication_then_second_restoration,
    )

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="complete recovery pair retained",
    ) as caught:
        historical_build._publish_historical_pair(stage, output)

    recovery_dirs = list(tmp_path.glob(".historical-rollback-*"))
    assert len(recovery_dirs) == 1
    recovery_dir = recovery_dirs[0]
    assert str(recovery_dir) in str(caught.value)
    assert {
        name: (recovery_dir / name).read_bytes() for name in old
    } == old


def test_publish_historical_pair_retains_prior_after_commit_cleanup_failure(
    monkeypatch, tmp_path
):
    """Would fail if old-pair cleanup invalidated a committed publication."""
    output = tmp_path / "parquet"
    stage = tmp_path / "stage"
    output.mkdir()
    stage.mkdir()
    old = {}
    new = {}
    for name in historical_build.HISTORICAL_PRODUCTS:
        old[name] = b"old " + name.encode()
        new[name] = b"new " + name.encode()
        (output / name).write_bytes(old[name])
        (stage / name).write_bytes(new[name])
    real_rmtree = historical_build.shutil.rmtree

    def retain_rollback(path):
        if Path(path).name.startswith(".historical-rollback-"):
            raise OSError("seeded rollback cleanup failure")
        real_rmtree(path)

    monkeypatch.setattr(historical_build.shutil, "rmtree", retain_rollback)

    recovery_dir = historical_build._publish_historical_pair(stage, output)

    assert recovery_dir is not None
    assert recovery_dir == next(tmp_path.glob(".historical-rollback-*"))
    assert {name: (output / name).read_bytes() for name in new} == new
    assert {
        name: (recovery_dir / name).read_bytes() for name in old
    } == old


def test_run_warns_when_committed_prior_pair_cleanup_is_retained(
    monkeypatch, tmp_path
):
    """Would fail if run hid a retained recovery path after commit."""
    monkeypatch.setattr(
        historical_build.historical_bref, "HISTORICAL_SEASONS", (1947,)
    )
    output = tmp_path / "parquet"
    output.mkdir()
    pd.DataFrame({"season": [1984]}).to_parquet(output / "games.parquet")
    seed_historical_references(tmp_path, {1947: 1})
    games = assembled_game_frame()
    report = historical_build.validate(games, {1947: 1})
    retained = tmp_path / ".historical-rollback-retained"
    monkeypatch.setattr(
        historical_build,
        "assemble",
        lambda data_dir: (games.copy(), report.copy()),
    )
    monkeypatch.setattr(
        historical_build,
        "_raw_provenance",
        lambda data_dir: {"basketball_reference": {"pages": []}},
    )
    monkeypatch.setattr(
        historical_build,
        "_reference_provenance",
        lambda data_dir: {},
    )
    monkeypatch.setattr(
        historical_build.build,
        "_dependency_versions",
        lambda project_root: {},
    )
    monkeypatch.setattr(
        historical_build,
        "_publish_historical_pair",
        lambda stage_dir, output_dir: retained,
    )

    with pytest.warns(
        RuntimeWarning,
        match=f"prior pair retained at {retained}",
    ):
        result = historical_build.run(tmp_path)

    assert result is None


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_raw_provenance_rejects_non_directory_cache_root(tmp_path, kind):
    """Would fail if provenance followed or traversed a non-directory root."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    cache_root = raw_dir / "historical_bref"
    if kind == "file":
        cache_root.write_bytes(b"not a directory")
    else:
        target = tmp_path / "cache-target"
        target.mkdir()
        cache_root.symlink_to(target, target_is_directory=True)

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="historical cache root must be a regular directory",
    ):
        historical_build._raw_provenance(tmp_path)


@pytest.mark.parametrize("target", ["body", "sidecar"])
@pytest.mark.parametrize("kind", ["directory", "symlink"])
def test_raw_provenance_rejects_non_regular_cache_entry(tmp_path, target, kind):
    """Would fail if a symlink or directory could be checksum-attested as raw data."""
    seed_verified_historical_cache(tmp_path)
    body = tmp_path / "raw/historical_bref/BAA_1947_games-november.html"
    path = body if target == "body" else body.with_name(body.name + ".meta.json")
    if kind == "directory":
        path.unlink()
        path.mkdir()
    else:
        replacement = tmp_path / f"replacement-{target}"
        replacement.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(replacement)

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="non-regular historical cache entry",
    ):
        historical_build._raw_provenance(tmp_path)


@pytest.mark.parametrize(
    "kind", ["stage-directory", "stage-symlink", "prior-directory", "prior-symlink"]
)
def test_publish_historical_pair_rejects_non_regular_products(tmp_path, kind):
    """Would fail if publisher products could be directories instead of files."""
    output = tmp_path / "parquet"
    stage = tmp_path / "stage"
    output.mkdir()
    stage.mkdir()
    for name in historical_build.HISTORICAL_PRODUCTS:
        (stage / name).write_bytes(b"stage")
        (output / name).write_bytes(b"prior")
    target = stage if kind.startswith("stage") else output
    product = target / historical_build.HISTORICAL_PRODUCTS[0]
    product.unlink()
    if kind.endswith("directory"):
        product.mkdir()
    else:
        replacement = tmp_path / f"replacement-{kind}"
        replacement.write_bytes(b"product")
        product.symlink_to(replacement)

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="non-regular historical publication product",
    ):
        historical_build._publish_historical_pair(stage, output)


def test_publish_historical_pair_rejects_orphan_stage_or_prior_pair(tmp_path):
    """Would fail if direct publisher callers could pass a one-file pair."""
    output = tmp_path / "parquet"
    stage = tmp_path / "stage"
    output.mkdir()
    stage.mkdir()
    (stage / historical_build.HISTORICAL_PRODUCTS[0]).write_bytes(b"stage")

    with pytest.raises(historical_build.HistoricalValidationError):
        historical_build._publish_historical_pair(stage, output)

    (stage / historical_build.HISTORICAL_PRODUCTS[1]).write_bytes(b"stage")
    (output / historical_build.HISTORICAL_PRODUCTS[0]).write_bytes(b"prior")
    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="historical publication pair is incomplete",
    ):
        historical_build._publish_historical_pair(stage, output)


def test_run_rejects_modern_publication_starting_after_1984(monkeypatch, tmp_path):
    """Would fail if historical publication could bridge a gap after 1983."""
    output = tmp_path / "parquet"
    output.mkdir()
    pd.DataFrame({"season": [1985]}).to_parquet(output / "games.parquet")
    monkeypatch.setattr(
        historical_build, "assemble", lambda data_dir: (assembled_game_frame(), {})
    )

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="modern publication must begin at ending season 1984",
    ):
        historical_build.run(tmp_path)


def test_run_preserves_prior_historical_pair_when_validation_fails(
    monkeypatch, tmp_path
):
    """Would fail if pre-publication validation could replace old history."""
    output = tmp_path / "parquet"
    output.mkdir()
    old = {
        "historical_games.parquet": b"old games",
        "historical_games_manifest.json": b"old manifest",
    }
    for name, payload in old.items():
        (output / name).write_bytes(payload)
    monkeypatch.setattr(
        historical_build,
        "assemble",
        lambda data_dir: (_ for _ in ()).throw(
            historical_build.HistoricalValidationError("seeded")
        ),
    )

    with pytest.raises(historical_build.HistoricalValidationError, match="seeded"):
        historical_build.run(tmp_path)

    assert {name: (output / name).read_bytes() for name in old} == old


def test_run_publishes_deterministic_attested_historical_manifest(
    monkeypatch, tmp_path
):
    """Would fail if historical publication leaked volatile or wrong provenance."""
    monkeypatch.setattr(
        historical_build.historical_bref, "HISTORICAL_SEASONS", (1947, 1983)
    )
    seed_verified_historical_cache(tmp_path)
    seed_historical_references(tmp_path, {1947: 1, 1983: 1})
    reference_dir = tmp_path / "reference"
    (reference_dir / "franchises.csv").write_bytes(
        historical_build.franchises.REFERENCE.read_bytes()
    )
    output = tmp_path / "parquet"
    output.mkdir()
    pd.DataFrame({"season": [1984]}).to_parquet(output / "games.parquet")
    games = assembled_game_frame()
    later = games.copy()
    later.loc[0, "game_id"] = "bref-198311010TOR"
    later.loc[0, "season"] = 1983
    later.loc[0, "league"] = "NBA"
    later.loc[0, "schedule_url"] = (
        "https://www.basketball-reference.com/leagues/"
        "NBA_1983_games-october.html"
    )
    games = pd.concat([games, later], ignore_index=True).astype(
        historical_build.HISTORICAL_DTYPES
    )
    report = historical_build.validate(games, {1947: 1, 1983: 1})
    monkeypatch.setattr(
        historical_build,
        "assemble",
        lambda data_dir: (games.copy(), report.copy()),
    )

    historical_build.run(tmp_path)
    first = {
        name: hashlib.sha256((output / name).read_bytes()).hexdigest()
        for name in historical_build.HISTORICAL_PRODUCTS
    }
    historical_build.run(tmp_path)
    second = {
        name: hashlib.sha256((output / name).read_bytes()).hexdigest()
        for name in historical_build.HISTORICAL_PRODUCTS
    }
    manifest = json.loads(
        (output / "historical_games_manifest.json").read_text(encoding="utf-8")
    )

    assert first == second
    assert manifest["schema_version"] == 1
    assert manifest["tables"]["historical_games"]["min_season"] == 1947
    assert manifest["tables"]["historical_games"]["max_season"] == 1983
    assert manifest["tables"]["historical_games"]["schema"] == [
        {"name": "game_id", "arrow_type": "large_string"},
        {"name": "season", "arrow_type": "int64"},
        {"name": "league", "arrow_type": "large_string"},
        {"name": "game_date", "arrow_type": "date32[day]"},
        {"name": "away_team_name", "arrow_type": "large_string"},
        {"name": "home_team_name", "arrow_type": "large_string"},
        {"name": "away_franchise_id", "arrow_type": "large_string"},
        {"name": "home_franchise_id", "arrow_type": "large_string"},
        {"name": "away_pts", "arrow_type": "int64"},
        {"name": "home_pts", "arrow_type": "int64"},
        {"name": "home_margin", "arrow_type": "int64"},
        {"name": "overtime_periods", "arrow_type": "int64"},
        {"name": "site_status", "arrow_type": "large_string"},
        {"name": "venue_name", "arrow_type": "large_string"},
        {"name": "attendance", "arrow_type": "int64"},
        {"name": "attendance_status", "arrow_type": "large_string"},
        {"name": "boxscore_url", "arrow_type": "large_string"},
        {"name": "schedule_url", "arrow_type": "large_string"},
    ]
    assert "fetched_at" not in json.dumps(manifest)
    assert "/home/" not in json.dumps(manifest)


def test_run_rejects_reordered_historical_parquet_after_reread(
    monkeypatch, tmp_path
):
    """Would fail if Parquet reordering could pass frame and report checks."""
    monkeypatch.setattr(
        historical_build.historical_bref, "HISTORICAL_SEASONS", (1947,)
    )
    seed_verified_historical_cache(tmp_path)
    seed_historical_references(tmp_path, {1947: 2})
    reference_dir = tmp_path / "reference"
    (reference_dir / "franchises.csv").write_bytes(
        historical_build.franchises.REFERENCE.read_bytes()
    )
    output = tmp_path / "parquet"
    output.mkdir()
    pd.DataFrame({"season": [1984]}).to_parquet(output / "games.parquet")
    games = assembled_game_frame()
    second = games.copy()
    second.loc[0, "game_id"] = "bref-194611020TRH"
    games = pd.concat([games, second], ignore_index=True).astype(
        historical_build.HISTORICAL_DTYPES
    )
    report = historical_build.validate(games, {1947: 2})
    monkeypatch.setattr(
        historical_build,
        "assemble",
        lambda data_dir: (games.copy(), report.copy()),
    )
    real_read_parquet = historical_build.pd.read_parquet

    def reordered_read_parquet(path, *args, **kwargs):
        frame = real_read_parquet(path, *args, **kwargs)
        if Path(path).name == "historical_games.parquet":
            return frame.iloc[::-1].reset_index(drop=True)
        return frame

    monkeypatch.setattr(
        historical_build.pd, "read_parquet", reordered_read_parquet
    )

    with pytest.raises(AssertionError):
        historical_build.run(tmp_path)


def test_assemble_resolves_lineage_and_orients_margin(monkeypatch, tmp_path):
    row = pd.DataFrame([source_game()])
    monkeypatch.setattr(
        historical_build.historical_bref,
        "HISTORICAL_SEASONS",
        (1947,),
    )
    monkeypatch.setattr(
        historical_build.historical_bref,
        "load_season",
        lambda raw_dir, season: row,
    )
    seed_historical_references(tmp_path, {1947: 1})

    games, report = historical_build.assemble(tmp_path)

    assert games.loc[0, "away_franchise_id"] == "NYK"
    assert games.loc[0, "home_franchise_id"] == "TOR_1946"
    assert games.loc[0, "home_margin"] == -2
    assert games.columns.tolist() == historical_build.HISTORICAL_COLUMNS
    assert report["games_by_season"] == {1947: 1}


def test_validation_rejects_wrong_season_count():
    games = assembled_game_frame()

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="historical game counts",
    ):
        historical_build.validate(games, {1947: 2})


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("game_id", pd.NA, "null game_id"),
        ("away_pts", -1, "negative score"),
        ("home_margin", 99, "home_margin disagreement"),
        ("overtime_periods", -1, "negative overtime"),
        ("site_status", "road", "unsupported site_status"),
        ("attendance", -1, "negative attendance"),
    ],
)
def test_validation_rejects_invalid_value(column, value, message):
    games = assembled_game_frame()
    games.loc[0, column] = value

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match=message,
    ):
        historical_build.validate(games, {1947: 1})


def test_validation_rejects_duplicate_game_id():
    games = pd.concat([assembled_game_frame(), assembled_game_frame()])

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="duplicate game_id",
    ):
        historical_build.validate(games, {1947: 2})


def test_validation_rejects_league_boundary():
    games = assembled_game_frame()
    games.loc[0, "league"] = "NBA"

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="league/season disagreement",
    ):
        historical_build.validate(games, {1947: 1})


def test_validation_rejects_equal_source_teams():
    games = assembled_game_frame()
    games.loc[0, "home_team_name"] = "New York Knicks"

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="identical source teams",
    ):
        historical_build.validate(games, {1947: 1})


def test_validation_rejects_attendance_status_disagreement():
    games = assembled_game_frame()
    games.loc[0, "attendance"] = 1

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="attendance/status disagreement",
    ):
        historical_build.validate(games, {1947: 1})


def test_validation_rejects_overtime_marker_disagreement():
    games = assembled_game_frame()
    evidence = pd.DataFrame([{
        "game_id": games.loc[0, "game_id"],
        "overtime_marker": "2OT",
        "overtime_periods": 0,
        "game_remarks": "",
    }])

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="overtime marker disagreement",
    ):
        historical_build.validate(games, {1947: 1}, evidence=evidence)


def test_validation_accumulates_multiple_diagnostics():
    games = assembled_game_frame()
    games.loc[0, "away_pts"] = -1
    games.loc[0, "site_status"] = "road"

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="negative score.*unsupported site_status",
    ):
        historical_build.validate(games, {1947: 1})


def test_validation_reports_incomplete_schema_without_crashing():
    games = pd.DataFrame({"game_id": ["bref-incomplete"]})

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="publication schema",
    ):
        historical_build.validate(games, {1947: 1})


def test_validation_reports_invalid_dtype_without_crashing():
    games = assembled_game_frame()
    games["season"] = games["season"].astype("string")

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="publication schema",
    ):
        historical_build.validate(games, {1947: 1})


def test_validation_reports_duplicate_columns_without_crashing():
    games = assembled_game_frame()
    games.loc[0, "away_pts"] = -1
    games = pd.concat([games, games[["season"]]], axis=1)

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="publication schema.*negative score",
    ):
        historical_build.validate(games, {1947: 1})


@pytest.mark.parametrize("value", ["1946-11-01", pd.Timestamp("1946-11-01")])
def test_validation_rejects_non_date_game_date(value):
    games = assembled_game_frame()
    games.loc[0, "game_date"] = value

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="non-date game_date",
    ):
        historical_build.validate(games, {1947: 1})


@pytest.mark.parametrize(
    ("season", "league"),
    [(1946, "BAA"), (1984, "NBA")],
)
def test_validation_rejects_league_outside_historical_era(season, league):
    games = assembled_game_frame()
    games.loc[0, "season"] = season
    games.loc[0, "league"] = league

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="league outside historical era",
    ):
        historical_build.validate(games, {season: 1})


def overtime_evidence(game_id="bref-194611010TRH", periods=0, marker=""):
    return pd.DataFrame([{
        "game_id": game_id,
        "overtime_marker": marker,
        "overtime_periods": periods,
        "game_remarks": "",
    }])


@pytest.mark.parametrize(
    "evidence",
    [
        pd.DataFrame(columns=[
            "game_id", "overtime_marker", "overtime_periods", "game_remarks",
        ]),
        overtime_evidence(game_id="bref-extra"),
    ],
)
def test_validation_requires_exact_overtime_evidence_game_ids(evidence):
    games = assembled_game_frame()

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="overtime evidence game_id alignment",
    ):
        historical_build.validate(games, {1947: 1}, evidence=evidence)


def test_validation_compares_published_overtime_to_aligned_evidence():
    games = assembled_game_frame()
    evidence = overtime_evidence(periods=1, marker="OT")

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="overtime periods disagreement",
    ):
        historical_build.validate(games, {1947: 1}, evidence=evidence)
