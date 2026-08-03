import importlib
import sys
from pathlib import Path

import pytest
import requests


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


def test_fetch_historical_forwards_only_requested_seasons(monkeypatch, tmp_path):
    cli = load_cli()
    raw_dir = Path(__file__).resolve().parents[1] / "data/raw"
    fetch_calls = []
    monkeypatch.setattr(
        cli.historical_bref,
        "fetch",
        lambda *args: fetch_calls.append(args),
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nba_data", "fetch-historical", "--season", "1947", "--season",
            "1960", "--season", "1983",
        ],
    )

    cli.main()

    assert fetch_calls == [(raw_dir, (1947, 1960, 1983))]


def test_fetch_historical_defaults_to_all_seasons(monkeypatch, tmp_path):
    cli = load_cli()
    raw_dir = Path(__file__).resolve().parents[1] / "data/raw"
    fetch_calls = []
    monkeypatch.setattr(
        cli.historical_bref,
        "fetch",
        lambda *args: fetch_calls.append(args),
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["nba_data", "fetch-historical"])

    cli.main()

    assert fetch_calls == [(raw_dir, cli.historical_bref.HISTORICAL_SEASONS)]


def test_build_historical_runs_from_repository_data_directory(monkeypatch, tmp_path):
    cli = load_cli()
    data_dir = Path(__file__).resolve().parents[1] / "data"
    build_calls = []
    monkeypatch.setattr(cli.historical_build, "run", build_calls.append)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["nba_data", "build-historical"])

    cli.main()

    assert build_calls == [data_dir]


def test_fetch_playoffs_one_season(monkeypatch, tmp_path):
    cli = load_cli()
    calls = []
    monkeypatch.setattr(
        cli.playoff_bref,
        "fetch",
        lambda raw, seasons: calls.append((raw, tuple(seasons))),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nba_data",
            "--data-dir",
            str(tmp_path),
            "fetch-playoffs",
            "--season",
            "1984",
        ],
    )

    cli.main()

    assert calls == [(tmp_path.resolve() / "raw", (1984,))]


def test_fetch_playoffs_defaults_to_all_dedicated_seasons(
    monkeypatch, tmp_path
):
    cli = load_cli()
    calls = []
    monkeypatch.setattr(
        cli.playoff_bref,
        "fetch",
        lambda raw, seasons: calls.append((raw, tuple(seasons))),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nba_data",
            "--data-dir",
            str(tmp_path),
            "fetch-playoffs",
        ],
    )

    cli.main()

    assert calls == [
        (
            tmp_path.resolve() / "raw",
            tuple(range(1984, 2027)),
        )
    ]


@pytest.mark.parametrize("season", [1983, 2027])
def test_fetch_playoffs_rejects_out_of_range_season(
    monkeypatch, tmp_path, season
):
    cli = load_cli()
    calls = []
    monkeypatch.setattr(
        cli.playoff_bref,
        "fetch",
        lambda *args: calls.append(args),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nba_data",
            "--data-dir",
            str(tmp_path),
            "fetch-playoffs",
            "--season",
            str(season),
        ],
    )

    with pytest.raises(SystemExit) as caught:
        cli.main()

    assert caught.value.code == 2
    assert calls == []


def test_build_playoffs_is_offline_dispatch(monkeypatch, tmp_path):
    cli = load_cli()
    calls = []
    monkeypatch.setattr(cli.playoff_build, "run", calls.append)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nba_data",
            "--data-dir",
            str(tmp_path),
            "build-playoffs",
        ],
    )

    cli.main()

    assert calls == [tmp_path.resolve()]


def test_fetch_pbp_defaults_to_all_seasons(monkeypatch, tmp_path):
    cli = load_cli()
    called = []
    monkeypatch.setattr(cli.pbp, "fetch", lambda data, seasons: called.append(
        (data, tuple(seasons))
    ))
    monkeypatch.setattr(
        sys,
        "argv",
        ["nba_data", "--data-dir", str(tmp_path), "fetch-pbp"],
    )
    cli.main()
    assert called == [(tmp_path, cli.pbp.SEASONS)]


def test_fetch_pbp_accepts_multiple_seasons(monkeypatch, tmp_path):
    cli = load_cli()
    called = []
    monkeypatch.setattr(cli.pbp, "fetch", lambda data, seasons: called.append(
        tuple(seasons)
    ))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nba_data",
            "--data-dir",
            str(tmp_path),
            "fetch-pbp",
            "--season",
            "1997",
            "--season",
            "2026",
        ],
    )
    cli.main()
    assert called == [(1997, 2026)]


def test_fetch_pbp_deduplicates_seasons_preserving_first_occurrence(
    monkeypatch, tmp_path
):
    """Would fail if repeated --season flags were sorted instead of
    normalized to first-occurrence command order."""
    cli = load_cli()
    called = []
    monkeypatch.setattr(
        cli.pbp,
        "fetch",
        lambda data, seasons: called.append(tuple(seasons)),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nba_data",
            "--data-dir",
            str(tmp_path),
            "fetch-pbp",
            "--season",
            "2026",
            "--season",
            "1997",
            "--season",
            "2026",
        ],
    )

    cli.main()

    assert called == [(2026, 1997)]


@pytest.mark.parametrize("season", [1996, 2027])
def test_fetch_pbp_rejects_out_of_range_season(monkeypatch, tmp_path, season):
    cli = load_cli()
    calls = []
    monkeypatch.setattr(cli.pbp, "fetch", lambda *args: calls.append(args))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nba_data",
            "--data-dir",
            str(tmp_path),
            "fetch-pbp",
            "--season",
            str(season),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2
    assert calls == []


def test_fetch_pbp_rejects_nonint_season(monkeypatch, tmp_path):
    cli = load_cli()
    calls = []
    monkeypatch.setattr(cli.pbp, "fetch", lambda *args: calls.append(args))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nba_data",
            "--data-dir",
            str(tmp_path),
            "fetch-pbp",
            "--season",
            "not-a-season",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2
    assert calls == []


def test_build_pbp_dispatches_offline_builder(monkeypatch, tmp_path):
    cli = load_cli()
    called = []

    def _raise_if_invoked(*args, **kwargs):
        raise AssertionError("network entry point invoked during build-pbp")

    monkeypatch.setattr(cli.pbp_build, "run", called.append)
    # Patch every network entry point reachable from the CLI module so a
    # regression that made build-pbp fall through to a fetch path fails
    # loudly instead of silently succeeding against the network.
    monkeypatch.setattr(cli.pbp, "fetch", _raise_if_invoked)
    monkeypatch.setattr(cli.stats_nba, "fetch", _raise_if_invoked)
    monkeypatch.setattr(cli.hoopr, "fetch", _raise_if_invoked)
    monkeypatch.setattr(cli.bref, "fetch", _raise_if_invoked)
    monkeypatch.setattr(cli.historical_bref, "fetch", _raise_if_invoked)
    monkeypatch.setattr(cli.playoff_bref, "fetch", _raise_if_invoked)
    monkeypatch.setattr(requests.Session, "get", _raise_if_invoked)
    monkeypatch.setattr(
        sys,
        "argv",
        ["nba_data", "--data-dir", str(tmp_path), "build-pbp"],
    )

    cli.main()

    assert called == [tmp_path]


def test_build_pbp_rejects_season_flag(monkeypatch, tmp_path):
    """Would fail if build-pbp silently accepted a --season it never uses."""
    cli = load_cli()
    calls = []
    monkeypatch.setattr(cli.pbp_build, "run", calls.append)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nba_data",
            "--data-dir",
            str(tmp_path),
            "build-pbp",
            "--season",
            "2026",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2
    assert calls == []


def test_fetch_player_logs_defaults_to_all_seasons(monkeypatch, tmp_path):
    """Would fail if the player-log command omitted its complete default scope."""
    cli = load_cli()
    calls = []
    provider = type(
        "PlayerLogs", (), {
            "SEASONS": (1984, 2026),
            "fetch": staticmethod(lambda data, seasons: calls.append(
                (data, tuple(seasons))
            )),
        },
    )
    monkeypatch.setattr(cli, "player_logs", provider, raising=False)
    monkeypatch.setattr(
        sys, "argv", ["nba_data", "--data-dir", str(tmp_path), "fetch-player-logs"]
    )

    cli.main()

    assert calls == [(tmp_path, (1984, 2026))]


def test_fetch_player_logs_deduplicates_seasons_in_first_occurrence_order(
    monkeypatch, tmp_path
):
    """Would fail if repeatable --season flags were sorted or repeated."""
    cli = load_cli()
    calls = []
    provider = type(
        "PlayerLogs", (), {
            "SEASONS": (1984, 2026),
            "fetch": staticmethod(lambda data, seasons: calls.append(
                (data, tuple(seasons))
            )),
        },
    )
    monkeypatch.setattr(cli, "player_logs", provider, raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nba_data", "--data-dir", str(tmp_path), "fetch-player-logs",
            "--season", "2026", "--season", "1984", "--season", "2026",
        ],
    )

    cli.main()

    assert calls == [(tmp_path, (2026, 1984))]


@pytest.mark.parametrize("season", ["1983", "2027", "not-a-season"])
def test_fetch_player_logs_rejects_invalid_seasons(monkeypatch, tmp_path, season):
    """Would fail if invalid CLI input reached the player-log fetcher."""
    cli = load_cli()
    calls = []
    provider = type(
        "PlayerLogs", (), {
            "SEASONS": (1984, 2026),
            "fetch": staticmethod(lambda *args: calls.append(args)),
        },
    )
    monkeypatch.setattr(cli, "player_logs", provider, raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nba_data", "--data-dir", str(tmp_path), "fetch-player-logs",
            "--season", season,
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2
    assert calls == []


def test_build_player_games_dispatches_only_offline_builder(monkeypatch, tmp_path):
    """Would fail if player-game build reached any fetch or HTTP entry point."""
    cli = load_cli()
    calls = []

    def network_forbidden(*args, **kwargs):
        raise AssertionError("network access is forbidden during build-player-games")

    for provider_name in (
        "stats_nba", "hoopr", "bref", "historical_bref", "playoff_bref",
        "pbp", "player_logs",
    ):
        provider = getattr(cli, provider_name)
        monkeypatch.setattr(provider, "fetch", network_forbidden)
    import requests
    monkeypatch.setattr(requests.Session, "get", network_forbidden)
    monkeypatch.setattr(
        cli,
        "player_games_build",
        type("PlayerGamesBuild", (), {
            "run": staticmethod(lambda data: calls.append(data)),
        }),
        raising=False,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["nba_data", "--data-dir", str(tmp_path), "build-player-games"],
    )

    cli.main()

    assert calls == [tmp_path]


def test_build_player_games_rejects_season_flag(monkeypatch, tmp_path):
    """Would fail if an online-style partial scope reached the offline builder."""
    cli = load_cli()
    calls = []
    monkeypatch.setattr(
        cli,
        "player_games_build",
        type("PlayerGamesBuild", (), {
            "run": staticmethod(lambda *args: calls.append(args)),
        }),
        raising=False,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nba_data", "--data-dir", str(tmp_path), "build-player-games",
            "--season", "2026",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2
    assert calls == []
