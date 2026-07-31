"""Pure analysis primitives for long-run NBA home-court results."""

import math
from pathlib import Path

import numpy as np
import pandas as pd

from nba_data import publication


RESULT_COLUMNS = [
    "game_id",
    "season",
    "league",
    "game_date",
    "home_franchise_id",
    "away_franchise_id",
    "home_pts",
    "away_pts",
    "home_margin",
    "site_status",
    "period",
]

SUMMARY_COLUMNS = [
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
    "home_win_ci_low",
    "home_win_ci_high",
    "mean_home_margin",
    "median_home_margin",
    "mean_margin_ci_low",
    "mean_margin_ci_high",
    "trailing_5_home_win_pct",
    "trailing_5_mean_home_margin",
]


def load_results_panel(data_dir: Path) -> pd.DataFrame:
    """Load compatible historical and modern result rows."""
    parquet = data_dir / "parquet"
    with publication.publication_lock(parquet, exclusive=False):
        historical = pd.read_parquet(
            parquet / "historical_games.parquet"
        ).assign(period="historical")
        modern = pd.read_parquet(parquet / "games.parquet").assign(
            league="NBA",
            home_margin=lambda frame: (
                frame["home_pts"] - frame["away_pts"]
            ),
            site_status=lambda frame: np.where(
                frame["neutral_site"], "neutral", "home"
            ),
            period="modern",
        )
    if (
        int(historical["season"].max()) != 1983
        or int(modern["season"].min()) != 1984
    ):
        raise ValueError(
            "results panel must meet the exact 1983/1984 boundary"
        )
    panel = pd.concat(
        [
            historical[RESULT_COLUMNS],
            modern[RESULT_COLUMNS],
        ],
        ignore_index=True,
    ).sort_values(
        ["season", "game_date", "game_id"],
        kind="stable",
    ).reset_index(drop=True)
    if panel["game_id"].duplicated().any():
        raise ValueError("results panel contains duplicate game_id")
    return panel


def wilson_interval(
    successes: int,
    trials: int,
    z: float = 1.959963984540054,
) -> tuple[float, float]:
    """Return Wilson score bounds for a binomial proportion."""
    if trials <= 0:
        return (math.nan, math.nan)
    proportion = successes / trials
    denominator = 1 + z * z / trials
    center = (
        proportion + z * z / (2 * trials)
    ) / denominator
    half_width = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / trials
            + z * z / (4 * trials * trials)
        )
        / denominator
    )
    low = max(0.0, min(proportion, center - half_width))
    high = min(1.0, max(proportion, center + half_width))
    return low, high


def cluster_bootstrap_mean_margin(
    groups: dict[str, np.ndarray],
    *,
    seed: int,
    resamples: int = 5000,
) -> tuple[float, float]:
    """Bootstrap mean margin by resampling whole franchise clusters."""
    if not groups:
        return (math.nan, math.nan)
    keys = tuple(sorted(groups))
    rng = np.random.default_rng(seed)
    draws = np.empty(resamples, dtype=float)
    for index in range(resamples):
        sampled = rng.choice(keys, size=len(keys), replace=True)
        margins = np.concatenate([groups[str(key)] for key in sampled])
        draws[index] = float(margins.mean())
    low, high = np.quantile(draws, [0.025, 0.975])
    return float(low), float(high)


def summarize_by_season(
    panel: pd.DataFrame,
    *,
    seed: int = 1947,
    resamples: int = 5000,
) -> pd.DataFrame:
    """Summarize home-site outcomes and pooled trailing estimates."""
    records = []
    for season, season_frame in panel.groupby("season", sort=True):
        home = season_frame.loc[season_frame["site_status"].eq("home")]
        games = len(season_frame)
        home_site_games = len(home)
        home_wins = int(home["home_margin"].gt(0).sum())
        win_low, win_high = wilson_interval(
            home_wins,
            home_site_games,
        )
        groups = {
            str(franchise): frame["home_margin"].to_numpy(dtype=float)
            for franchise, frame in home.groupby(
                "home_franchise_id",
                sort=False,
            )
        }
        margin_low, margin_high = cluster_bootstrap_mean_margin(
            groups,
            seed=seed + int(season),
            resamples=resamples,
        )
        records.append({
            "season": season,
            "league": season_frame["league"].iloc[0],
            "games": games,
            "home_site_games": home_site_games,
            "neutral_site_games": int(
                season_frame["site_status"].eq("neutral").sum()
            ),
            "unknown_site_games": int(
                season_frame["site_status"].eq("unknown").sum()
            ),
            "home_site_coverage": home_site_games / games,
            "home_wins": home_wins,
            "home_losses": home_site_games - home_wins,
            "home_win_pct": (
                home_wins / home_site_games
                if home_site_games
                else math.nan
            ),
            "home_win_ci_low": win_low,
            "home_win_ci_high": win_high,
            "mean_home_margin": (
                float(home["home_margin"].mean())
                if home_site_games
                else math.nan
            ),
            "median_home_margin": (
                float(home["home_margin"].median())
                if home_site_games
                else math.nan
            ),
            "mean_margin_ci_low": margin_low,
            "mean_margin_ci_high": margin_high,
            "_home_margin_sum": float(home["home_margin"].sum()),
        })
    summary = pd.DataFrame.from_records(records)
    summary["trailing_5_home_win_pct"] = (
        summary["home_wins"].rolling(5, min_periods=1).sum()
        / summary["home_site_games"].rolling(5, min_periods=1).sum()
    )
    summary["trailing_5_mean_home_margin"] = (
        summary["_home_margin_sum"].rolling(5, min_periods=1).sum()
        / summary["home_site_games"].rolling(5, min_periods=1).sum()
    )
    return summary[SUMMARY_COLUMNS]
