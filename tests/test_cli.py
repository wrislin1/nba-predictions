import importlib
import sys
from pathlib import Path

import pytest


class FetchCallRecorder:
    def __init__(self) -> None:
        self.calls = []

    def for_provider(self, provider):
        def record(raw_dir, seasons):
            self.calls.append((provider, raw_dir, list(seasons)))

        return record


def load_cli():
    return importlib.import_module("nba_data.__main__")


def install_fetch_recorders(monkeypatch, cli):
    recorder = FetchCallRecorder()
    for provider in ("stats_nba", "hoopr", "bref"):
        monkeypatch.setattr(
            getattr(cli, provider),
            "fetch",
            recorder.for_provider(provider),
        )
    return recorder


def test_fetch_calls_providers_in_order_for_all_seasons(
    monkeypatch,
    tmp_path,
):
    cli = load_cli()
    recorder = install_fetch_recorders(monkeypatch, cli)
    raw_dir = Path(__file__).resolve().parents[1] / "data/raw"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["nba_data", "fetch"])

    cli.main()

    assert recorder.calls == [
        ("stats_nba", raw_dir, list(range(2020, 2027))),
        ("hoopr", raw_dir, list(range(2020, 2027))),
        ("bref", raw_dir, list(range(2020, 2027))),
    ]


def test_fetch_forwards_one_requested_season(monkeypatch, tmp_path):
    cli = load_cli()
    recorder = install_fetch_recorders(monkeypatch, cli)
    raw_dir = Path(__file__).resolve().parents[1] / "data/raw"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["nba_data", "fetch", "--season", "2024"],
    )

    cli.main()

    assert recorder.calls == [
        ("stats_nba", raw_dir, [2024]),
        ("hoopr", raw_dir, [2024]),
        ("bref", raw_dir, [2024]),
    ]


def test_inspect_venues_writes_inventory_without_fetching(
    monkeypatch,
    tmp_path,
):
    cli = load_cli()
    recorder = install_fetch_recorders(monkeypatch, cli)
    data_dir = Path(__file__).resolve().parents[1] / "data"
    monkeypatch.chdir(tmp_path)
    inventory_calls = []
    monkeypatch.setattr(
        cli.build,
        "write_venue_inventory",
        inventory_calls.append,
    )
    monkeypatch.setattr(sys, "argv", ["nba_data", "inspect-venues"])

    cli.main()

    assert inventory_calls == [data_dir]
    assert recorder.calls == []


def test_build_publishes_from_repository_data_directory(
    monkeypatch,
    tmp_path,
):
    cli = load_cli()
    recorder = install_fetch_recorders(monkeypatch, cli)
    data_dir = Path(__file__).resolve().parents[1] / "data"
    monkeypatch.chdir(tmp_path)
    build_calls = []
    monkeypatch.setattr(cli.build, "run", build_calls.append, raising=False)
    monkeypatch.setattr(sys, "argv", ["nba_data", "build"])

    cli.main()

    assert build_calls == [data_dir]
    assert recorder.calls == []


def test_build_accepts_explicit_data_directory(
    tmp_path,
    monkeypatch,
):
    """Would fail if an installed CLI derived data from site-packages."""
    cli = load_cli()
    recorder = install_fetch_recorders(monkeypatch, cli)
    data_dir = tmp_path / "data"
    build_calls = []
    monkeypatch.setattr(cli.build, "run", build_calls.append)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nba_data",
            "--data-dir",
            str(data_dir),
            "build",
        ],
    )

    cli.main()

    assert build_calls == [data_dir.resolve()]
    assert recorder.calls == []


def test_installed_cli_requires_explicit_data_directory(
    monkeypatch,
    tmp_path,
    capsys,
):
    cli = load_cli()
    monkeypatch.setattr(
        cli,
        "__file__",
        str(tmp_path / "site-packages/nba_data/__main__.py"),
    )
    monkeypatch.setattr(cli.build, "run", lambda data_dir: None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["nba_data", "build"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 2
    assert "--data-dir" in capsys.readouterr().err


@pytest.mark.parametrize("season", [2019, 2027])
def test_invalid_season_exits_before_fetching(monkeypatch, season):
    cli = load_cli()
    recorder = install_fetch_recorders(monkeypatch, cli)
    monkeypatch.setattr(
        sys,
        "argv",
        ["nba_data", "fetch", "--season", str(season)],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2
    assert recorder.calls == []
