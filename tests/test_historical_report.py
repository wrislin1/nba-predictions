import hashlib
import importlib.util
from pathlib import Path

import pandas as pd
from PIL import Image

from nba_data import home_court


_REPORT_PATH = (
    Path(__file__).resolve().parents[1]
    / "analysis/covid_hca/build_historical_trend.py"
)
_REPORT_SPEC = importlib.util.spec_from_file_location(
    "build_historical_trend", _REPORT_PATH
)
historical_report = importlib.util.module_from_spec(_REPORT_SPEC)
_REPORT_SPEC.loader.exec_module(historical_report)


def synthetic_summary_1947_to_2026():
    seasons = list(range(1947, 2027))
    frame = pd.DataFrame({
        "season": seasons,
        "league": [
            "BAA" if season <= 1949 else "NBA" for season in seasons
        ],
        "games": 100,
        "home_site_games": 90,
        "neutral_site_games": 6,
        "unknown_site_games": 4,
        "home_site_coverage": 0.9,
        "home_wins": 54,
        "home_losses": 36,
        "home_win_pct": 0.6,
        "home_win_ci_low": 0.55,
        "home_win_ci_high": 0.65,
        "mean_home_margin": 3.2,
        "median_home_margin": 3.0,
        "mean_margin_ci_low": 2.4,
        "mean_margin_ci_high": 4.0,
        "trailing_5_home_win_pct": 0.6,
        "trailing_5_mean_home_margin": 3.2,
    })
    return frame.loc[:, home_court.SUMMARY_COLUMNS]


def summary_with_distinct_report_values():
    summary = synthetic_summary_1947_to_2026()
    values = {
        1947: {
            "home_win_pct": 0.512,
            "mean_home_margin": 1.25,
            "trailing_5_home_win_pct": 0.512,
        },
        1951: {
            "trailing_5_home_win_pct": 0.681,
        },
        1983: {
            "home_win_pct": 0.577,
            "mean_home_margin": 3.01,
        },
        1999: {
            "trailing_5_home_win_pct": 0.498,
        },
        2020: {
            "home_win_pct": 0.521,
            "mean_home_margin": 0.31,
        },
        2026: {
            "home_win_pct": 0.612,
            "mean_home_margin": 4.27,
        },
    }
    for season, columns in values.items():
        for column, value in columns.items():
            summary.loc[summary["season"].eq(season), column] = value
    return summary


def test_report_run_writes_three_deterministic_products(
    tmp_path,
    monkeypatch,
):
    summary = synthetic_summary_1947_to_2026()
    monkeypatch.setattr(
        historical_report.home_court,
        "load_results_panel",
        lambda data_dir: pd.DataFrame(),
    )
    monkeypatch.setattr(
        historical_report.home_court,
        "summarize_by_season",
        lambda panel: summary,
    )
    output = tmp_path / "analysis"
    historical_report.run(tmp_path, output)
    first = {
        path.relative_to(output): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in output.rglob("*")
        if path.is_file()
    }
    historical_report.run(tmp_path, output)
    second = {
        path.relative_to(output): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in output.rglob("*")
        if path.is_file()
    }
    assert first == second
    assert set(first) == {
        Path("data/home_court_by_season.csv"),
        Path("figures/home_court_trend_1947_2026.png"),
        Path("historical_trend.md"),
    }
    written_summary = pd.read_csv(
        output / "data/home_court_by_season.csv"
    )
    assert written_summary.columns.tolist() == home_court.SUMMARY_COLUMNS
    assert written_summary["season"].tolist() == list(range(1947, 2027))
    report_bytes = (output / "historical_trend.md").read_bytes()
    assert report_bytes.endswith(b"\n")
    assert b"\r" not in report_bytes
    with Image.open(
        output / "figures/home_court_trend_1947_2026.png"
    ) as figure:
        assert figure.format == "PNG"
        assert figure.width > figure.height > 600
        assert figure.info["Software"] == "nba-data historical trend"
        figure.verify()


def test_render_report_uses_supplied_rows_and_labels_noncausal():
    report = historical_report.render_report(
        summary_with_distinct_report_values()
    )

    for heading in [
        "## What is measured",
        "## Long-run pattern",
        "## COVID context",
        "## Data limitations",
        "## Reproduce",
    ]:
        assert heading in report
    assert (
        "The inaugural 1947 estimate is 51.2% home wins with a mean "
        "home margin of +1.25 points."
    ) in report
    assert (
        "The trailing five-season home-win trend peaks in 1951 at 68.1% "
        "and reaches its minimum in 1999 at 49.8%."
    ) in report
    assert (
        "At the historical/modern data-source boundary, the 1983 "
        "estimate is 57.7% and +3.01 points."
    ) in report
    assert (
        "The 2020 bubble-period estimate is 52.1% and +0.31 points."
    ) in report
    assert (
        "The latest 2026 estimate is 61.2% and +4.27 points."
    ) in report
    assert "Era markers are descriptive, not causal." in report
    assert (
        "Historical site-classification quality changes across eras."
    ) in report
    assert (
        "1984–2019 inherits the legacy home-designation assumption."
    ) in report
    assert (
        "The later COVID fixed-effects analysis—not this chart—defines "
        "the causal estimand."
    ) in report


def test_render_figure_has_two_readable_panels_and_all_markers(
    tmp_path,
    monkeypatch,
):
    real_close = historical_report.plt.close
    rendered = []
    monkeypatch.setattr(
        historical_report.plt,
        "close",
        lambda figure: rendered.append(figure),
    )

    historical_report.render_figure(
        synthetic_summary_1947_to_2026(),
        tmp_path / "trend.png",
    )

    assert len(rendered) == 1
    figure = rendered[0]
    try:
        assert len(figure.axes) == 2
        win_axis, margin_axis = figure.axes
        assert win_axis.get_ylim()[0] > 40
        assert margin_axis.get_ylim()[0] < 0 < margin_axis.get_ylim()[1]
        vertical_positions = {
            float(line.get_xdata()[0])
            for line in win_axis.lines
            if len(line.get_xdata()) == 2
            and line.get_xdata()[0] == line.get_xdata()[1]
        }
        assert vertical_positions == {1955.0, 1977.0, 1980.0, 1983.5, 2020.0}
        assert {text.get_text() for text in win_axis.texts} == {
            "24-second clock",
            "ABA merger",
            "3-point line",
            "COVID/bubble",
            "1983/1984 data-source boundary",
        }
    finally:
        real_close(figure)


def test_cli_uses_checkout_defaults_and_accepts_overrides(
    tmp_path,
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(
        historical_report,
        "run",
        lambda data_dir, output_dir: calls.append((data_dir, output_dir)),
    )
    checkout = _REPORT_PATH.parents[2]

    historical_report.main([])
    historical_report.main([
        "--data-dir",
        str(tmp_path / "input"),
        "--output-dir",
        str(tmp_path / "output"),
    ])

    assert calls == [
        (checkout / "data", checkout / "analysis/covid_hca"),
        (tmp_path / "input", tmp_path / "output"),
    ]
