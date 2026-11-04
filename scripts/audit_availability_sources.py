#!/usr/bin/env python3
"""Render offline review artifacts for the availability sources.

Loads the ``player_games`` worklist and the raw trees through
``availability_build.assemble_part_a`` / ``assemble_part_b`` with the
committed exception ledger but WITHOUT the build's gates: every observed
disagreement is reported as a ledger candidate, never raised. Computes
``availability_resolve.resolution_metrics`` and
``availability_build.derivative_agreement`` and writes, into an absent or
empty output directory:

``review.md``
    roster consistency and roster-size outliers, parse-status and
    title-check counts, placeholder counts, resolution rates with the team,
    month and status breakdowns, unresolved names and matchups,
    listed-but-not-rostered rows, derivative disagreement patterns,
    unmatched versions, proposed ledger candidates and (with
    ``--gamebook-sample``) the gamebook sample.
``resolution.json``
    ``resolution_metrics`` per season.
``disagreement_patterns.json``
    ``derivative_agreement`` per season.
``ledger_candidates.json``
    every observed entry in approved-entry shape with ``"note": ""``, as a
    ledger document the owner can copy entries from.
``gamebook_sample.json``
    (``--gamebook-sample``) ``gamebooks.select_gamebook_sample`` with each
    game's ``gamebook_url``.

Every artifact is deterministic: no timestamps, no absolute paths.

Usage::

    python scripts/audit_availability_sources.py --data-dir data --output-dir /tmp/availability-review [--gamebook-sample]
"""

from __future__ import annotations

import argparse
import json
import stat
import sys
from pathlib import Path

from nba_data import availability_build, availability_resolve, box_rosters, gamebooks, injury_derivative


JSON_FILES = ("resolution.json", "disagreement_patterns.json", "ledger_candidates.json")
TOP_NAMES = 50
TOP_PATTERNS = 50
TOP_NOT_OUT = 100
TOP_UNMATCHED = 100


class AuditError(ValueError):
    """Raised when the audit's output directory is unusable."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="audit_availability_sources.py")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--gamebook-sample", action="store_true")
    return parser


def prepare_output_dir(output_dir: Path) -> Path:
    """Require an absent or empty real directory (``player_games_build.write_source_review``)."""
    output_dir = Path(output_dir)
    try:
        observed = output_dir.lstat()
    except FileNotFoundError:
        observed = None
    if observed is not None:
        if not stat.S_ISDIR(observed.st_mode):
            raise AuditError(f"review output is not a real directory: {output_dir}")
        if any(output_dir.iterdir()):
            raise AuditError(f"review output directory must be empty: {output_dir}")
    else:
        output_dir.mkdir(parents=True)
    return output_dir


def _json_bytes(document: object) -> bytes:
    return (json.dumps(document, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _table(header: list[str], rows: list[list[object]]) -> list[str]:
    def cell(value: object) -> str:
        if value is None:
            return "null"
        if isinstance(value, float):
            return f"{value:.4f}"
        return str(value).replace("|", "\\|").replace("\n", " ")

    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines.extend("| " + " | ".join(cell(value) for value in row) + " |" for row in rows)
    return lines


def ledger_candidates(observed: dict) -> dict:
    """The observed entries as a ledger document with empty notes."""
    document = {"schema_version": availability_build.LEDGER_SCHEMA_VERSION}
    for section in availability_build.LEDGER_SECTIONS:
        document[section] = {
            key: {**entry, "note": ""} for key, entry in sorted(observed[section].items())
        }
    return document


def _candidate_status(section: str, key: str, entry: dict, approved: dict) -> str:
    approval = approved[section].get(key)
    if approval is None:
        return "new"
    if section == "team_resolution" and entry["nba_team_id"] is None:
        return "new"
    stripped = {k: v for k, v in approval.items() if k != "note"}
    return "approved" if stripped == entry else "drift"


def render_review(
    *,
    index: box_rosters.PlayerGamesIndex,
    part_a: availability_build.PartA,
    part_b: availability_build.PartB,
    metrics: dict,
    agreement: dict,
    observed: dict,
    approved: dict,
    sizes: dict,
    sample: list[dict] | None,
) -> str:
    seasons = sorted(metrics)
    lines = [
        "# Availability source review",
        "",
        f"Games: {len(index.games)}",
        f"Box-roster pairs: {2 * len(index.games)}",
        f"Reports present: {len(part_b.versions)}",
        f"Reports parsed: {int((part_b.versions['parse_status'] != 'excluded').sum())}",
        f"Listing rows: {len(part_b.listings)}",
        "",
        "## Slots per grid",
        "",
        *_table(
            ["grid", "present", "unavailable", "rechecked"],
            [
                [grid, totals["present"], totals["unavailable"], totals["rechecked"]]
                for grid, totals in sorted(part_b.slot_totals.items())
            ],
        ),
        "",
        "## Roster consistency",
        "",
        f"Appearance disagreements observed: {len(part_a.appearance_observed)}",
        "",
        *_table(
            ["key", "comment_raw", "minutes_raw", "appearance"],
            [
                [key, entry["comment_raw"], entry["minutes_raw"], entry["appearance"]]
                for key, entry in part_a.appearance_observed.items()
            ],
        ),
        "",
        f"Schedule-date disagreements observed: {len(part_a.schedule_observed)}",
        "",
        *_table(
            ["game_id", "game_et_raw", "game_code_raw", "game_date"],
            [
                [key, entry["game_et_raw"], entry["game_code_raw"], entry["game_date"]]
                for key, entry in part_a.schedule_observed.items()
            ],
        ),
        "",
        "## Roster sizes",
        "",
        *_table(
            ["season", "team-games", "active min", "active max", "empty inactive lists"],
            [
                [season, entry["team_games"], entry["active_min"], entry["active_max"], entry["inactive_empty"]]
                for season, entry in sorted(sizes["by_season"].items())
            ],
        ),
        "",
        f"Roster-size outliers (active < 8, active > 15 or no inactives): {len(sizes['outliers'])}",
        "",
        *_table(
            ["game_id", "nba_team_id", "active", "inactive"],
            [
                [entry["game_id"], entry["nba_team_id"], entry["active"], entry["inactive"]]
                for entry in sizes["outliers"]
            ],
        ),
        "",
        "## Parse status and title checks",
        "",
        *_table(
            [
                "season", *availability_build.PARSE_STATUSES,
                *(f"title {check}" for check in availability_build.TITLE_CHECKS),
            ],
            [
                [
                    season,
                    *(part_b.parse_counts[season].get(status, 0) for status in availability_build.PARSE_STATUSES),
                    *(
                        part_b.title_counts.get(season, {}).get(check, 0)
                        for check in availability_build.TITLE_CHECKS
                    ),
                ]
                for season in sorted(part_b.parse_counts)
            ],
        ),
        "",
        f"Observed report_versions entries (excluded or title-accepted): {len(part_b.report_versions_observed)}",
        "",
        *_table(
            ["report_id", "action", "failed_check", "title_printed_et", "slot_label", "sha256"],
            [
                [
                    report_id, entry["action"], entry["failed_check"], entry["title_printed_et"],
                    entry["slot_label"], entry["sha256"],
                ]
                for report_id, entry in part_b.report_versions_observed.items()
            ],
        ),
        "",
        "## Placeholder counts",
        "",
        *_table(
            ["season", "phrase", "rows"],
            [
                [season, phrase, rows]
                for season in sorted(part_b.placeholder_counts)
                for phrase, rows in sorted(part_b.placeholder_counts[season].items())
            ],
        ),
        "",
        "## Resolution",
        "",
        *_table(
            [
                "season", "rows", "rows resolved", "row rate", "identities", "identities resolved",
                "identity rate",
            ],
            [
                [
                    season, metrics[season]["rows"], metrics[season]["rows_resolved"],
                    metrics[season]["row_rate"], metrics[season]["identities"],
                    metrics[season]["identities_resolved"], metrics[season]["identity_rate"],
                ]
                for season in seasons
            ],
        ),
        "",
    ]
    for breakdown, label in (
        ("identity_rate_by_team", "nba_team_id"),
        ("identity_rate_by_month", "month"),
        ("identity_rate_by_status", "status"),
    ):
        lines.extend([
            f"### Identity rate by {label}",
            "",
            *_table(
                ["season", label, "identity rate"],
                [
                    [season, key, rate]
                    for season in seasons
                    for key, rate in metrics[season][breakdown].items()
                ],
            ),
            "",
        ])
    lines.extend([
        "## Unresolved names",
        "",
        f"Top {TOP_NAMES} per season by row count (every name is in resolution.json).",
        "",
        *_table(
            ["season", "player_name_raw", "rows", "identities"],
            [
                [season, name, entry["rows"], entry["identities"]]
                for season in seasons
                for name, entry in sorted(
                    metrics[season]["unresolved_names"].items(),
                    key=lambda item: (-item[1]["rows"], item[0]),
                )[:TOP_NAMES]
            ],
        ),
        "",
        "## Unresolved matchups",
        "",
        *_table(
            ["season", "matchup", "rows"],
            [
                [season, matchup, rows]
                for season in seasons
                for matchup, rows in metrics[season]["unresolved_matchups"].items()
            ],
        ),
        "",
        "## Listed but not rostered",
        "",
        *_table(
            ["season", "rows", "identities"],
            [
                [season, metrics[season]["listed_not_rostered"]["rows"],
                 metrics[season]["listed_not_rostered"]["identities"]]
                for season in seasons
            ],
        ),
        "",
        "### By team",
        "",
        *_table(
            ["season", "nba_team_id", "rows", "identities"],
            [
                [season, team, entry["rows"], entry["identities"]]
                for season in seasons
                for team, entry in metrics[season]["listed_not_rostered"]["by_team"].items()
            ],
        ),
        "",
        "### By status",
        "",
        *_table(
            ["season", "status", "rows", "identities"],
            [
                [season, status, entry["rows"], entry["identities"]]
                for season in seasons
                for status, entry in metrics[season]["listed_not_rostered"]["by_status"].items()
            ],
        ),
        "",
        f"### Not Out (first {TOP_NOT_OUT} per season; every row is in resolution.json)",
        "",
        *_table(
            ["season", "report_id", "row_number", "game_id", "player_name_raw", "status"],
            [
                [season, *row]
                for season in seasons
                for row in metrics[season]["listed_not_rostered"]["not_out_rows"][:TOP_NOT_OUT]
            ],
        ),
        "",
        "## Derivative agreement",
        "",
        *_table(
            [
                "season", "asset", "versions ours", "excluded", "versions theirs", "compared",
                "disagreeing", "agree all", "agree except reason", "only ours", "only theirs",
                "artifacts", "null report_datetime", "unmatched ours", "unmatched theirs",
            ],
            [
                [
                    season, "yes" if entry["asset_present"] else "no", entry["versions_ours"],
                    entry["versions_excluded"], entry["versions_theirs"], entry["versions_compared"],
                    entry["versions_disagreeing"], entry["agree_all"], entry["agree_except_reason"],
                    entry["only_ours"], entry["only_theirs"], entry["derivative_artifacts"],
                    entry["null_report_datetime"], len(entry["unmatched_ours"]), len(entry["unmatched_theirs"]),
                ]
                for season, entry in sorted(agreement.items())
            ],
        ),
        "",
        f"### Disagreement patterns (top {TOP_PATTERNS} per season; every pattern is in disagreement_patterns.json)",
        "",
        *_table(
            ["season", "pattern", "count", "versions"],
            [
                [season, pattern, detail["count"], len(detail["report_ids"])]
                for season, entry in sorted(agreement.items())
                for pattern, detail in sorted(
                    entry["patterns"].items(), key=lambda item: (-item[1]["count"], item[0]),
                )[:TOP_PATTERNS]
            ],
        ),
        "",
        "## Unmatched versions",
        "",
        f"First {TOP_UNMATCHED} per season and side; every version is in disagreement_patterns.json.",
        "",
        *_table(
            ["season", "side", "version"],
            [
                [season, side, version]
                for season, entry in sorted(agreement.items())
                for side, key in (("ours", "unmatched_ours"), ("theirs", "unmatched_theirs"))
                for version in entry[key][:TOP_UNMATCHED]
            ],
        ),
        "",
        "## Ledger candidates",
        "",
        "Every observed entry in approved shape (ledger_candidates.json carries them with an empty note); "
        "`status` compares each with the committed ledger: new, approved (identical), drift.",
        "",
        *_table(
            ["section", "key", "status", "entry"],
            [
                [
                    section, key, _candidate_status(section, key, entry, approved),
                    json.dumps(entry, sort_keys=True, ensure_ascii=False),
                ]
                for section in availability_build.LEDGER_SECTIONS
                for key, entry in sorted(observed[section].items())
            ],
        ),
        "",
        "Approved entries not observed (stale):",
        "",
        *_table(
            ["section", "key"],
            [
                [section, key]
                for section in availability_build.LEDGER_SECTIONS
                for key in sorted(set(approved[section]) - set(observed[section]))
            ],
        ),
        "",
    ])
    if sample is not None:
        lines.extend([
            "## Gamebook sample",
            "",
            "A case without a game (`null`) could not be filled from this tree or is owner-selected.",
            "",
            *_table(
                ["case", "season", "game_id", "game_date", "nba_team_id", "gamebook_url"],
                [
                    [entry["case"], entry["season"], entry["game_id"], entry["game_date"],
                     entry["nba_team_id"], entry["gamebook_url"]]
                    for entry in sample
                ],
            ),
            "",
        ])
    return "\n".join(lines)


def audit(data_dir: Path, *, gamebook_sample: bool) -> dict[str, bytes]:
    """Compute every review artifact as ``{file name: bytes}``."""
    data_dir = Path(data_dir)
    raw_root = data_dir / "raw"
    approved = availability_build.load_exceptions()
    index = box_rosters.load_player_games_index(data_dir / box_rosters.PLAYER_GAMES_SUBDIR)
    part_a = availability_build.assemble_part_a(raw_root, index, approved)
    part_b = availability_build.assemble_part_b(raw_root, part_a, approved)
    metrics = availability_resolve.resolution_metrics(part_b.listings)
    agreement = availability_build.derivative_agreement(part_b, data_dir / injury_derivative.RAW_SUBDIR)
    sizes = availability_build.roster_sizes(part_a)
    observed = {
        "appearance_consistency": part_a.appearance_observed,
        "schedule_dates": part_a.schedule_observed,
        "report_versions": part_b.report_versions_observed,
        "team_resolution": part_b.team_resolution_observed,
    }
    sample = None
    if gamebook_sample:
        sample = [
            {
                **entry,
                "gamebook_url": (
                    None if entry["game_code_raw"] is None else gamebooks.gamebook_url(entry["game_code_raw"])
                ),
            }
            for entry in gamebooks.select_gamebook_sample(part_a.schedule, part_a.roster_counts)
        ]
    artifacts = {
        "review.md": (render_review(
            index=index, part_a=part_a, part_b=part_b, metrics=metrics, agreement=agreement,
            observed=observed, approved=approved, sizes=sizes, sample=sample,
        ) + "\n").encode("utf-8"),
        "resolution.json": _json_bytes({str(season): entry for season, entry in metrics.items()}),
        "disagreement_patterns.json": _json_bytes({str(season): entry for season, entry in agreement.items()}),
        "ledger_candidates.json": _json_bytes(ledger_candidates(observed)),
    }
    if sample is not None:
        artifacts["gamebook_sample.json"] = _json_bytes(sample)
    return artifacts


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    data_dir = args.data_dir.expanduser().resolve()
    output_dir = prepare_output_dir(args.output_dir.expanduser())
    artifacts = audit(data_dir, gamebook_sample=args.gamebook_sample)
    for name, payload in sorted(artifacts.items()):
        (output_dir / name).write_bytes(payload)
    print(f"wrote {len(artifacts)} review artifacts to {output_dir}")


if __name__ == "__main__":
    try:
        main()
    except AuditError as exc:
        print(f"audit_availability_sources.py: {exc}", file=sys.stderr)
        sys.exit(2)
