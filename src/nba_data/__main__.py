import argparse
from pathlib import Path

from nba_data import bref, build, hoopr, stats_nba


def main() -> None:
    parser = argparse.ArgumentParser(prog="nba_data")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.cwd() / "data",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    fetch_parser = commands.add_parser("fetch")
    fetch_parser.add_argument("--season", type=int, choices=range(2020, 2027))
    commands.add_parser("inspect-venues")
    commands.add_parser("build")
    args = parser.parse_args()
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


if __name__ == "__main__":
    main()
