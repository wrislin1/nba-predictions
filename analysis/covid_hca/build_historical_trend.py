"""Build deterministic long-run NBA home-court trend deliverables."""

import argparse
from collections.abc import Sequence
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd

from nba_data import home_court


ERA_MARKERS = {
    1955: "24-second clock",
    1977: "ABA merger",
    1980: "3-point line",
    2020: "COVID/bubble",
}
SOURCE_BOUNDARY = 1983.5
_CHECKOUT_ROOT = Path(__file__).resolve().parents[2]


def percentage(value: float) -> str:
    """Format a proportion as a percentage with one decimal place."""
    return f"{100 * value:.1f}%"


def margin(value: float) -> str:
    """Format a point margin with an explicit sign."""
    return f"{value:+.2f} points"


def _series(summary: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(summary[column], errors="raise")


def render_figure(summary: pd.DataFrame, path: Path) -> None:
    """Render annual and trailing home-court trends as a two-panel PNG."""
    seasons = _series(summary, "season").to_numpy(dtype=float)
    win_pct = 100 * _series(summary, "home_win_pct").to_numpy(dtype=float)
    win_low = 100 * _series(
        summary, "home_win_ci_low"
    ).to_numpy(dtype=float)
    win_high = 100 * _series(
        summary, "home_win_ci_high"
    ).to_numpy(dtype=float)
    trailing_win = 100 * _series(
        summary, "trailing_5_home_win_pct"
    ).to_numpy(dtype=float)
    mean_margin = _series(
        summary, "mean_home_margin"
    ).to_numpy(dtype=float)
    margin_low = _series(
        summary, "mean_margin_ci_low"
    ).to_numpy(dtype=float)
    margin_high = _series(
        summary, "mean_margin_ci_high"
    ).to_numpy(dtype=float)
    trailing_margin = _series(
        summary, "trailing_5_mean_home_margin"
    ).to_numpy(dtype=float)

    figure, (win_axis, margin_axis) = plt.subplots(
        2,
        1,
        figsize=(13, 8),
        sharex=True,
        constrained_layout=False,
    )
    try:
        win_axis.fill_between(
            seasons,
            win_low,
            win_high,
            color="#4c78a8",
            alpha=0.18,
            linewidth=0,
            label="95% Wilson interval",
        )
        win_axis.plot(
            seasons,
            win_pct,
            color="#4c78a8",
            linewidth=1.15,
            alpha=0.8,
            label="Annual estimate",
        )
        win_axis.plot(
            seasons,
            trailing_win,
            color="#0b3558",
            linewidth=2.4,
            label="Pooled trailing five seasons",
        )
        margin_axis.fill_between(
            seasons,
            margin_low,
            margin_high,
            color="#f58518",
            alpha=0.18,
            linewidth=0,
            label="95% cluster-bootstrap interval",
        )
        margin_axis.plot(
            seasons,
            mean_margin,
            color="#f58518",
            linewidth=1.15,
            alpha=0.8,
            label="Annual estimate",
        )
        margin_axis.plot(
            seasons,
            trailing_margin,
            color="#8a3f00",
            linewidth=2.4,
            label="Pooled trailing five seasons",
        )

        for axis in (win_axis, margin_axis):
            axis.grid(axis="y", color="#d9d9d9", linewidth=0.7, alpha=0.7)
            for season in ERA_MARKERS:
                axis.axvline(
                    season,
                    color="#777777",
                    linestyle=":",
                    linewidth=0.9,
                    alpha=0.8,
                )
            axis.axvline(
                SOURCE_BOUNDARY,
                color="#7a3e9d",
                linestyle=(0, (5, 3)),
                linewidth=1.5,
                alpha=0.95,
            )
            axis.spines[["top", "right"]].set_visible(False)
        margin_axis.axhline(
            0,
            color="#777777",
            linewidth=0.7,
            alpha=0.45,
        )

        marker_offsets = {1955: 0.0, 1977: 0.0, 1980: 0.13, 2020: 0.0}
        for season, label in ERA_MARKERS.items():
            win_axis.text(
                season + 0.25,
                0.96 - marker_offsets[season],
                label,
                transform=win_axis.get_xaxis_transform(),
                rotation=90,
                va="top",
                ha="left",
                fontsize=8,
                color="#555555",
            )
        win_axis.text(
            SOURCE_BOUNDARY + 0.35,
            0.96,
            "1983/1984 data-source boundary",
            transform=win_axis.get_xaxis_transform(),
            rotation=90,
            va="top",
            ha="left",
            fontsize=8,
            color="#7a3e9d",
        )

        win_axis.set_title(
            "Home-court advantage across BAA/NBA history",
            loc="left",
            fontsize=15,
            fontweight="bold",
        )
        win_axis.set_ylabel("Home win percentage points")
        margin_axis.set_ylabel("Mean home margin (points)")
        margin_axis.set_xlabel("Season")
        win_axis.legend(loc="lower left", frameon=False, ncols=3)
        margin_axis.legend(loc="lower left", frameon=False, ncols=3)
        margin_axis.set_xlim(1946, 2027)
        margin_axis.set_xticks(range(1950, 2027, 10))
        figure.align_ylabels()
        figure.subplots_adjust(
            left=0.08,
            right=0.98,
            top=0.93,
            bottom=0.09,
            hspace=0.14,
        )

        path.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(
            path,
            dpi=160,
            bbox_inches="tight",
            metadata={"Software": "nba-data historical trend"},
        )
    finally:
        plt.close(figure)


def _season_row(summary: pd.DataFrame, season: int) -> pd.Series:
    rows = summary.loc[_series(summary, "season").eq(season)]
    if len(rows) != 1:
        raise ValueError(f"summary must contain exactly one {season} row")
    return rows.iloc[0]


def render_report(summary: pd.DataFrame) -> str:
    """Render a data-derived, explicitly descriptive Markdown report."""
    inaugural = _season_row(summary, 1947)
    boundary = _season_row(summary, 1983)
    bubble = _season_row(summary, 2020)
    latest = _season_row(summary, 2026)
    trailing = summary.loc[
        _series(summary, "trailing_5_home_win_pct").notna()
    ]
    maximum = trailing.loc[
        _series(trailing, "trailing_5_home_win_pct").idxmax()
    ]
    minimum = trailing.loc[
        _series(trailing, "trailing_5_home_win_pct").idxmin()
    ]

    return (
        "# Home-court advantage across BAA/NBA history\n"
        "\n"
        "## What is measured\n"
        "\n"
        "The chart summarizes regular-season games classified as true home "
        "sites. Annual home-win estimates use Wilson intervals; annual mean "
        "home margins use franchise-cluster bootstrap intervals. The darker "
        "lines pool the trailing five seasons by game rather than averaging "
        "season estimates.\n"
        "\n"
        f"The inaugural 1947 estimate is "
        f"{percentage(float(inaugural['home_win_pct']))} home wins with a "
        f"mean home margin of "
        f"{margin(float(inaugural['mean_home_margin']))}.\n"
        "\n"
        "## Long-run pattern\n"
        "\n"
        "The trailing five-season home-win trend peaks in "
        f"{int(maximum['season'])} at "
        f"{percentage(float(maximum['trailing_5_home_win_pct']))} and "
        "reaches its minimum in "
        f"{int(minimum['season'])} at "
        f"{percentage(float(minimum['trailing_5_home_win_pct']))}.\n"
        "\n"
        "At the historical/modern data-source boundary, the 1983 estimate "
        f"is {percentage(float(boundary['home_win_pct']))} and "
        f"{margin(float(boundary['mean_home_margin']))}.\n"
        "\n"
        "Era markers are descriptive, not causal.\n"
        "\n"
        "## COVID context\n"
        "\n"
        "The 2020 bubble-period estimate is "
        f"{percentage(float(bubble['home_win_pct']))} and "
        f"{margin(float(bubble['mean_home_margin']))}.\n"
        "\n"
        "The latest 2026 estimate is "
        f"{percentage(float(latest['home_win_pct']))} and "
        f"{margin(float(latest['mean_home_margin']))}.\n"
        "\n"
        "The later COVID fixed-effects analysis—not this chart—defines the "
        "causal estimand.\n"
        "\n"
        "## Data limitations\n"
        "\n"
        "Historical site-classification quality changes across eras. "
        "Neutral and unknown sites are excluded from the home-site "
        "estimands, so coverage varies by season. 1984–2019 inherits the "
        "legacy home-designation assumption.\n"
        "\n"
        "## Reproduce\n"
        "\n"
        "From the repository root, run:\n"
        "\n"
        "```bash\n"
        "MPLBACKEND=Agg .venv/bin/python "
        "analysis/covid_hca/build_historical_trend.py\n"
        "```\n"
    )


def run(data_dir: Path, output_dir: Path) -> None:
    """Write the exact CSV, PNG, and Markdown historical trend products."""
    panel = home_court.load_results_panel(data_dir)
    summary = home_court.summarize_by_season(panel)
    data_path = output_dir / "data/home_court_by_season.csv"
    figure_path = (
        output_dir / "figures/home_court_trend_1947_2026.png"
    )
    report_path = output_dir / "historical_trend.md"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    figure_path.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(data_path, index=False, lineterminator="\n")
    render_figure(summary, figure_path)
    report_path.write_text(
        render_report(summary),
        encoding="utf-8",
        newline="\n",
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Parse CLI paths and build the historical trend products."""
    parser = argparse.ArgumentParser(
        description="Build deterministic historical home-court trends."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=_CHECKOUT_ROOT / "data",
        help="Data directory containing the parquet/ products.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_CHECKOUT_ROOT / "analysis/covid_hca",
        help="Destination for the CSV, PNG, and Markdown products.",
    )
    args = parser.parse_args(argv)
    run(args.data_dir, args.output_dir)


if __name__ == "__main__":
    main()
