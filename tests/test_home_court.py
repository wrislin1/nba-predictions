import datetime
import math
import threading

import numpy as np
import pandas as pd
import pytest

from nba_data import home_court, publication


def write_historical_parquet(
    data_dir,
    *,
    season,
    home_pts=100,
    away_pts=90,
    site_status="home",
):
    parquet = data_dir / "parquet"
    parquet.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{
        "game_id": f"bref-hist-{season}",
        "season": season,
        "league": "BAA" if season <= 1949 else "NBA",
        "game_date": datetime.date(season, 1, 15),
        "home_franchise_id": "BOS",
        "away_franchise_id": "NYK",
        "home_pts": home_pts,
        "away_pts": away_pts,
        "home_margin": home_pts - away_pts,
        "site_status": site_status,
    }]).to_parquet(parquet / "historical_games.parquet", index=False)


def write_modern_parquet(
    data_dir,
    *,
    season,
    home_pts=100,
    away_pts=90,
    neutral_site=False,
):
    parquet = data_dir / "parquet"
    parquet.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{
        "game_id": f"modern-{season}",
        "season": season,
        "game_date": datetime.date(season, 1, 15),
        "home_franchise_id": "BOS",
        "away_franchise_id": "NYK",
        "home_pts": home_pts,
        "away_pts": away_pts,
        "neutral_site": neutral_site,
    }]).to_parquet(parquet / "games.parquet", index=False)


def result_panel(rows):
    records = []
    for index, (season, franchise, site_status, margin) in enumerate(rows):
        records.append({
            "game_id": f"panel-{index:02d}",
            "season": season,
            "league": "BAA" if season <= 1949 else "NBA",
            "game_date": datetime.date(season, 2, 1),
            "home_franchise_id": franchise,
            "away_franchise_id": "OPP",
            "home_pts": 100 + margin,
            "away_pts": 100,
            "home_margin": margin,
            "site_status": site_status,
            "period": "historical",
        })
    return pd.DataFrame(records)


def test_load_results_panel_joins_exact_1983_1984_boundary(tmp_path):
    write_historical_parquet(
        tmp_path,
        season=1983,
        home_pts=90,
        away_pts=95,
        site_status="home",
    )
    write_modern_parquet(
        tmp_path,
        season=1984,
        home_pts=110,
        away_pts=100,
        neutral_site=False,
    )

    panel = home_court.load_results_panel(tmp_path)

    assert panel.columns.tolist() == home_court.RESULT_COLUMNS
    assert panel["season"].tolist() == [1983, 1984]
    assert panel["home_margin"].tolist() == [-5, 10]
    assert panel["site_status"].tolist() == ["home", "home"]
    assert panel["period"].tolist() == ["historical", "modern"]
    assert panel["league"].tolist() == ["NBA", "NBA"]


def test_load_results_panel_holds_shared_lock_across_both_parquet_reads(
    monkeypatch,
    tmp_path,
):
    """Would fail if an exclusive writer could enter between table reads."""
    write_historical_parquet(tmp_path, season=1983)
    write_modern_parquet(tmp_path, season=1984)
    parquet = tmp_path / "parquet"
    real_read_parquet = home_court.pd.read_parquet
    read_count = 0
    read_count_lock = threading.Lock()
    first_read = threading.Event()
    second_read = threading.Event()
    allow_first_read_to_return = threading.Event()
    allow_second_read_to_return = threading.Event()
    writer_attempting = threading.Event()
    writer_acquired = threading.Event()
    failures = []
    panels = []

    def paused_read_parquet(*args, **kwargs):
        nonlocal read_count
        frame = real_read_parquet(*args, **kwargs)
        with read_count_lock:
            read_count += 1
            current_read = read_count
        if current_read == 1:
            first_read.set()
            assert allow_first_read_to_return.wait(timeout=5)
        elif current_read == 2:
            second_read.set()
            assert allow_second_read_to_return.wait(timeout=5)
        return frame

    def load_panel():
        try:
            panels.append(home_court.load_results_panel(tmp_path))
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    def observe_with_exclusive_lock():
        try:
            writer_attempting.set()
            with publication.publication_lock(parquet, exclusive=True):
                writer_acquired.set()
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    monkeypatch.setattr(
        home_court.pd,
        "read_parquet",
        paused_read_parquet,
    )
    reader = threading.Thread(target=load_panel, daemon=True)
    writer = threading.Thread(
        target=observe_with_exclusive_lock,
        daemon=True,
    )
    reader.start()
    try:
        assert first_read.wait(timeout=5)
        writer.start()
        assert writer_attempting.wait(timeout=5)
        assert not writer_acquired.wait(timeout=0.2)

        allow_first_read_to_return.set()
        assert second_read.wait(timeout=5)
        assert not writer_acquired.wait(timeout=0.2)

        allow_second_read_to_return.set()
        reader.join(timeout=5)
        assert not reader.is_alive()
        assert writer_acquired.wait(timeout=5)
        writer.join(timeout=5)
        assert not writer.is_alive()
    finally:
        allow_first_read_to_return.set()
        allow_second_read_to_return.set()
        reader.join(timeout=5)
        if writer.ident is not None:
            writer.join(timeout=5)

    assert failures == []
    assert len(panels) == 1


@pytest.mark.parametrize(
    ("historical_season", "modern_season"),
    [(1982, 1984), (1983, 1983)],
)
def test_union_rejects_gap_or_overlap(
    tmp_path,
    historical_season,
    modern_season,
):
    write_historical_parquet(tmp_path, season=historical_season)
    write_modern_parquet(tmp_path, season=modern_season)

    with pytest.raises(ValueError, match="1983/1984 boundary"):
        home_court.load_results_panel(tmp_path)


def test_load_results_panel_maps_modern_neutral_site(tmp_path):
    write_historical_parquet(tmp_path, season=1983)
    write_modern_parquet(tmp_path, season=1984, neutral_site=True)

    panel = home_court.load_results_panel(tmp_path)

    assert panel.loc[panel["period"].eq("modern"), "site_status"].item() == (
        "neutral"
    )


def test_load_results_panel_sorts_by_season_date_and_game_id(tmp_path):
    write_historical_parquet(tmp_path, season=1983)
    historical_path = tmp_path / "parquet/historical_games.parquet"
    historical = pd.read_parquet(historical_path)
    historical = pd.concat(
        [
            historical.assign(
                game_id="bref-z",
                game_date=datetime.date(1983, 2, 1),
            ),
            historical.assign(
                game_id="bref-b",
                game_date=datetime.date(1983, 1, 1),
            ),
            historical.assign(
                game_id="bref-a",
                game_date=datetime.date(1983, 1, 1),
            ),
        ],
        ignore_index=True,
    )
    historical.to_parquet(historical_path, index=False)
    write_modern_parquet(tmp_path, season=1984)

    panel = home_court.load_results_panel(tmp_path)

    assert panel["game_id"].tolist() == [
        "bref-a",
        "bref-b",
        "bref-z",
        "modern-1984",
    ]


def test_load_results_panel_rejects_duplicate_game_ids(tmp_path):
    write_historical_parquet(tmp_path, season=1983)
    write_modern_parquet(tmp_path, season=1984)
    modern_path = tmp_path / "parquet/games.parquet"
    modern = pd.read_parquet(modern_path).assign(game_id="bref-hist-1983")
    modern.to_parquet(modern_path, index=False)

    with pytest.raises(ValueError, match="duplicate game_id"):
        home_court.load_results_panel(tmp_path)


def test_summary_excludes_neutral_and_unknown_from_home_estimands():
    panel = result_panel([
        (1947, "BOS", "home", 5),
        (1947, "NYK", "home", -1),
        (1947, "BOS", "neutral", 20),
        (1947, "NYK", "unknown", 30),
    ])

    summary = home_court.summarize_by_season(
        panel, seed=7, resamples=50
    )

    assert summary.columns.tolist() == home_court.SUMMARY_COLUMNS
    row = summary.iloc[0]
    assert row["games"] == 4
    assert row["home_site_games"] == 2
    assert row["neutral_site_games"] == 1
    assert row["unknown_site_games"] == 1
    assert row["home_site_coverage"] == 0.5
    assert row["home_wins"] == 1
    assert row["home_losses"] == 1
    assert row["home_win_pct"] == 0.5
    assert row["mean_home_margin"] == 2.0
    assert row["median_home_margin"] == 2.0


def test_trailing_window_is_game_weighted_not_season_weighted():
    panel = result_panel([
        (1947, "BOS", "home", 1),
        (1948, "BOS", "home", -1),
        (1948, "NYK", "home", -2),
        (1948, "PHI", "home", -3),
    ])

    summary = home_court.summarize_by_season(
        panel, seed=9, resamples=20
    )
    row = summary.loc[summary["season"].eq(1948)].iloc[0]

    assert row["trailing_5_home_win_pct"] == 0.25
    assert row["trailing_5_mean_home_margin"] == -1.25


def test_trailing_window_uses_only_five_seasons():
    panel = result_panel([
        (1947, "BOS", "home", 100),
        (1948, "BOS", "home", -1),
        (1949, "BOS", "home", -1),
        (1950, "BOS", "home", -1),
        (1951, "BOS", "home", -1),
        (1952, "BOS", "home", -1),
    ])

    summary = home_court.summarize_by_season(
        panel, seed=10, resamples=20
    )
    row = summary.loc[summary["season"].eq(1952)].iloc[0]

    assert row["trailing_5_home_win_pct"] == 0.0
    assert row["trailing_5_mean_home_margin"] == -1.0


@pytest.mark.parametrize(
    ("successes", "trials"),
    [(0, 10), (1, 3), (5, 10), (10, 10)],
)
def test_wilson_interval_contains_observed_proportion_and_is_bounded(
    successes,
    trials,
):
    low, high = home_court.wilson_interval(successes, trials)
    observed = successes / trials

    assert 0.0 <= low <= observed <= high <= 1.0


def test_wilson_interval_returns_nan_bounds_for_zero_trials():
    low, high = home_court.wilson_interval(0, 0)

    assert math.isnan(low)
    assert math.isnan(high)


def test_cluster_bootstrap_is_independent_of_group_insertion_order():
    groups = {
        "NYK": np.array([-5.0, 2.0]),
        "BOS": np.array([1.0, 4.0, 6.0]),
        "PHI": np.array([-2.0]),
    }

    interval = home_court.cluster_bootstrap_mean_margin(
        groups,
        seed=19,
        resamples=200,
    )
    reversed_interval = home_court.cluster_bootstrap_mean_margin(
        dict(reversed(tuple(groups.items()))),
        seed=19,
        resamples=200,
    )

    assert interval == reversed_interval


def test_cluster_bootstrap_returns_nan_bounds_for_no_groups():
    low, high = home_court.cluster_bootstrap_mean_margin(
        {},
        seed=19,
        resamples=20,
    )

    assert math.isnan(low)
    assert math.isnan(high)


def test_summary_is_deterministic_for_the_same_seed():
    panel = result_panel([
        (1947, "BOS", "home", 5),
        (1947, "BOS", "home", 3),
        (1947, "NYK", "home", -2),
        (1948, "BOS", "home", 1),
        (1948, "NYK", "home", -4),
        (1948, "PHI", "home", 7),
    ])

    first = home_court.summarize_by_season(
        panel,
        seed=23,
        resamples=200,
    )
    second = home_court.summarize_by_season(
        panel,
        seed=23,
        resamples=200,
    )

    pd.testing.assert_frame_equal(first, second)


def test_different_seed_does_not_change_point_estimates():
    panel = result_panel([
        (1947, "BOS", "home", 5),
        (1947, "BOS", "home", 3),
        (1947, "NYK", "home", -2),
        (1948, "BOS", "home", 1),
        (1948, "NYK", "home", -4),
        (1948, "PHI", "home", 7),
    ])
    point_columns = [
        "season",
        "league",
        "games",
        "home_site_games",
        "neutral_site_games",
        "unknown_site_games",
        "home_site_coverage",
        "home_wins",
        "home_losses",
        "home_win_pct",
        "mean_home_margin",
        "median_home_margin",
        "trailing_5_home_win_pct",
        "trailing_5_mean_home_margin",
    ]

    first = home_court.summarize_by_season(
        panel,
        seed=23,
        resamples=200,
    )
    second = home_court.summarize_by_season(
        panel,
        seed=24,
        resamples=200,
    )

    pd.testing.assert_frame_equal(
        first[point_columns],
        second[point_columns],
    )
