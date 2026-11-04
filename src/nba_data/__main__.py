import argparse
from pathlib import Path

from nba_data import (
    box_rosters,
    bref,
    build,
    historical_bref,
    historical_build,
    hoopr,
    pbp,
    pbp_build,
    player_games_build,
    player_logs,
    playoff_bref,
    playoff_build,
    stats_nba,
)


def _source_checkout_data_dir() -> Path | None:
    repository_root = Path(__file__).resolve().parents[2]
    data_dir = repository_root / "data"
    if (
        (repository_root / "pyproject.toml").is_file()
        and (data_dir / "reference").is_dir()
    ):
        return data_dir
    return None


def main() -> None:
    parser = argparse.ArgumentParser(prog="nba_data")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    fetch_parser = commands.add_parser("fetch")
    fetch_parser.add_argument("--season", type=int, choices=range(2020, 2027))
    historical_fetch = commands.add_parser("fetch-historical")
    historical_fetch.add_argument(
        "--season",
        type=int,
        action="append",
        choices=historical_bref.HISTORICAL_SEASONS,
    )
    playoff_fetch = commands.add_parser("fetch-playoffs")
    playoff_fetch.add_argument(
        "--season",
        type=int,
        action="append",
        choices=playoff_bref.DEDICATED_SEASONS,
    )
    commands.add_parser("inspect-venues")
    commands.add_parser("build")
    commands.add_parser("build-historical")
    commands.add_parser("build-playoffs")
    fetch_pbp = commands.add_parser("fetch-pbp")
    fetch_pbp.add_argument(
        "--season",
        type=int,
        action="append",
        choices=pbp.SEASONS,
    )
    fetch_player_logs = commands.add_parser("fetch-player-logs")
    fetch_player_logs.add_argument(
        "--season",
        type=int,
        action="append",
        choices=player_logs.SOURCE_SEASONS,
    )
    fetch_box_rosters = commands.add_parser("fetch-box-rosters")
    fetch_box_rosters.add_argument(
        "--season",
        type=int,
        action="append",
        choices=box_rosters.PUBLICATION_SEASONS,
    )
    commands.add_parser("build-pbp")
    commands.add_parser("build-player-games")
    args = parser.parse_args()
    if args.data_dir is None:
        data = _source_checkout_data_dir()
        if data is None:
            parser.error(
                "--data-dir is required outside a source checkout"
            )
    else:
        data = args.data_dir.expanduser().resolve()
    raw = data / "raw"
    seasons = [args.season] if getattr(args, "season", None) else range(2020, 2027)
    if args.command == "fetch":
        stats_nba.fetch(raw, seasons)
        hoopr.fetch(raw, seasons)
        bref.fetch(raw, seasons)
    elif args.command == "inspect-venues":
        build.write_venue_inventory(data)
    elif args.command == "build":
        build.run(data)
    elif args.command == "fetch-historical":
        selected = (
            tuple(args.season)
            if args.season
            else historical_bref.HISTORICAL_SEASONS
        )
        historical_bref.fetch(raw, selected)
    elif args.command == "build-historical":
        historical_build.run(data)
    elif args.command == "fetch-playoffs":
        selected = (
            tuple(args.season)
            if args.season
            else playoff_bref.DEDICATED_SEASONS
        )
        playoff_bref.fetch(raw, selected)
    elif args.command == "build-playoffs":
        playoff_build.run(data)
    elif args.command == "fetch-pbp":
        selected = (
            tuple(dict.fromkeys(args.season)) if args.season else pbp.SEASONS
        )
        pbp.fetch(data, selected)
    elif args.command == "fetch-player-logs":
        selected = (
            tuple(dict.fromkeys(args.season))
            if args.season
            else player_logs.SOURCE_SEASONS
        )
        player_logs.fetch(data, selected)
    elif args.command == "fetch-box-rosters":
        selected = (
            tuple(sorted(set(args.season)))
            if args.season
            else box_rosters.PUBLICATION_SEASONS
        )
        box_rosters.fetch(data, selected)
    elif args.command == "build-pbp":
        pbp_build.run(data)
    elif args.command == "build-player-games":
        player_games_build.run(data)


if __name__ == "__main__":
    main()
