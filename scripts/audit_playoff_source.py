"""Render deterministic observations from verified playoff schedule caches."""

import argparse
import hashlib
from pathlib import Path

from nba_data import bref_schedule, playoff_bref


def _resource_summary(
    data_dir: Path,
    season: int,
    resource: str,
    path: Path,
) -> dict[str, object]:
    metadata = playoff_bref.attest_cache_entry(path)
    payload = path.read_bytes()
    frame = bref_schedule.parse_schedule_page(
        payload,
        season=season,
        league="NBA",
        schedule_url=metadata["url"],
    )
    return {
        "season": season,
        "resource": resource,
        "path": path.relative_to(data_dir).as_posix(),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "rows": len(frame),
        "first_date": frame["game_date"].min().isoformat(),
        "last_date": frame["game_date"].max().isoformat(),
        "boxscore_links": int(frame["boxscore_url"].notna().sum()),
        "venue_present": int(frame["venue_name"].notna().sum()),
        "attendance_present": int(frame["attendance"].notna().sum()),
        "max_overtime": int(frame["overtime_periods"].max()),
    }


def audit_cache(data_dir: Path, seasons: tuple[int, ...]) -> str:
    """Render a deterministic audit of checksum-valid cached sources."""
    raw_dir = data_dir / "raw"
    cache_dir = raw_dir / "playoff_bref"
    summaries = []
    classifiers = []
    for season in seasons:
        dedicated = cache_dir / playoff_bref.playoff_filename(season)
        summaries.append(
            _resource_summary(
                data_dir, season, "dedicated", dedicated
            )
        )
        if season in playoff_bref.PLAY_IN_MONTHS:
            month = playoff_bref.PLAY_IN_MONTHS[season]
            play_in = cache_dir / f"NBA_{season}_games-{month}.html"
            summaries.append(
                _resource_summary(
                    data_dir, season, "play_in_month", play_in
                )
            )
        classifiers.append(playoff_bref.load_modern_season(raw_dir, season))

    lines = [
        "# Playoff BREF Source Spike",
        "",
        "Generated offline from checksum-valid cached playoff schedule pages.",
        "",
        "| season | resource | path | sha256 | rows | first_date | "
        "last_date | boxscore_links | venue_present | attendance_present | "
        "max_overtime |",
        "|---:|---|---|---|---:|---|---|---:|---:|---:|---:|",
    ]
    for summary in sorted(
        summaries,
        key=lambda value: (
            value["season"],
            value["resource"],
            value["path"],
        ),
    ):
        lines.append(
            "| {season} | {resource} | {path} | {sha256} | {rows} | "
            "{first_date} | {last_date} | {boxscore_links} | "
            "{venue_present} | {attendance_present} | {max_overtime} |".format(
                **summary
            )
        )

    lines.extend([
        "",
        "## Deterministic handling",
        "",
        "Each verified body must contain exactly one supported schedule table; "
        "every completed row must have both scores and a valid box-score link. "
        "Blank venue or attendance values remain null and are counted above. "
        "The 2020 bubble sources report venues but no attendance, so all 2020 "
        "attendance counts are zero without imputation. Play-in month pages "
        "contain regular-season rows; only exact `Play-In Game` remarks are "
        "classified below.",
        "",
        "## Classifier rows",
        "",
    ])
    classified = sorted(
        (
            row
            for frame in classifiers
            for row in frame.to_dict(orient="records")
        ),
        key=lambda row: (
            row["season"],
            row["game_date"],
            row["game_id"],
        ),
    )
    for row in classified:
        lines.append((
            f"{row['game_id']} | {row['game_date'].isoformat()} | "
            f"{row['game_type']} | {row['away_team_name']} "
            f"{row['away_pts']} | {row['home_team_name']} "
            f"{row['home_pts']} | {row['game_remarks']}"
        ).rstrip())
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data")
    )
    parser.add_argument(
        "--season", type=int, action="append", required=True
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rendered = audit_cache(args.data_dir, tuple(args.season))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
