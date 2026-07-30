"""Render deterministic observations from cached historical schedules."""

import argparse
from collections import Counter
from pathlib import Path

from nba_data import historical_bref


SPIKE_SEASONS = (1947, 1960, 1983)


def audit_season(raw_dir: Path, season: int) -> dict:
    """Summarize one checksum-valid cached historical season."""
    frame = historical_bref.load_season(raw_dir, season)
    return {
        "season": season,
        "league": historical_bref.league_for_season(season),
        "games": len(frame),
        "date_min": frame["game_date"].min().isoformat(),
        "date_max": frame["game_date"].max().isoformat(),
        "teams": sorted(
            set(frame["away_team_name"]) | set(frame["home_team_name"])
        ),
        "overtime_markers": dict(sorted(Counter(frame["overtime_marker"]).items())),
        "boxscore_links": int(frame["boxscore_url"].notna().sum()),
        "venue_reported": int(frame["venue_name"].notna().sum()),
        "attendance": dict(sorted(Counter(frame["attendance_status"]).items())),
        "site_status": dict(sorted(Counter(frame["site_status"]).items())),
        "nonblank_remarks": sorted(
            set(frame.loc[frame["game_remarks"].ne(""), "game_remarks"])
        ),
        "schedule_urls": sorted(frame["schedule_url"].unique()),
    }


def render_markdown(audits: list[dict]) -> str:
    """Render cached-season observations as a deterministic Markdown audit."""
    lines = [
        "# Historical Basketball Reference source spike",
        "",
        "Generated offline from checksum-valid cached index/month pages.",
        "",
        "## Discovery and boundary",
        "",
        "Only `{league}_{season}_games-{month}.html` links are accepted; "
        "`games-playoffs.html` is excluded.",
        "Rows after an exact `Playoffs` separator are postseason. Four "
        "verified duplicate `Playoffs` layouts classify rows before the first "
        "separator, except for two verified 1953 carryovers between separators. "
        "Exact `Tiebreaker` and `Protest; does not count toward record` rows "
        "are excluded as postseason or voided games. 1947 and 1980 use a "
        "documented first-playoff-game fallback when no separator exists.",
        "",
        "## Schema and statistic keys",
        "",
        "The schedule table must provide `date_game`, `visitor_team_name`, "
        "`visitor_pts`, `home_team_name`, `home_pts`, `box_score_text`, and "
        "`overtimes`. Rows with both score cells blank are not games.",
        "",
        "## Observed seasons",
        "",
    ]
    for audit in audits:
        lines.extend([
            f"### {audit['season']} {audit['league']}",
            "",
            f"- Games: {audit['games']}",
            f"- Dates: {audit['date_min']} through {audit['date_max']}",
            f"- Teams: {', '.join(audit['teams'])}",
            f"- Box-score links: {audit['boxscore_links']}",
            f"- Venue reported: {audit['venue_reported']}",
            f"- Attendance statuses: {audit['attendance']}",
            f"- Site statuses: {audit['site_status']}",
            f"- Overtime markers: {audit['overtime_markers']}",
            f"- Nonblank remarks: {audit['nonblank_remarks']}",
            f"- Schedule pages: {audit['schedule_urls']}",
            "",
        ])
    lines.extend([
        "## Missingness and normalization decisions",
        "",
        "| Source value | Normalized value |",
        "|---|---|",
        "| blank attendance | null + `missing` |",
        "| literal 0 attendance | 0 + `reported_zero` |",
        "| blank venue | null |",
        "| blank / OT / nOT | 0 / 1 / n overtime periods |",
        "| valid / blank box href | slug ID / deterministic fallback ID |",
        "| blank / explicit neutral / other remarks | `home` / `neutral` / `unknown` |",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    """Render either the source spike or every cached historical season."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--all-seasons", action="store_true")
    args = parser.parse_args()
    seasons = (
        historical_bref.HISTORICAL_SEASONS
        if args.all_seasons
        else SPIKE_SEASONS
    )
    rendered = render_markdown([
        audit_season(args.raw_dir, season)
        for season in seasons
    ])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
