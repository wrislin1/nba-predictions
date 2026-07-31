"""Parse Basketball-Reference schedule pages into normalized game rows."""

import re
from typing import NamedTuple
from urllib.parse import urljoin

from lxml import html
import pandas as pd


REQUIRED_STATS = frozenset({
    "date_game", "visitor_team_name", "visitor_pts", "home_team_name",
    "home_pts", "box_score_text", "overtimes",
})
OVERTIME_PATTERN = re.compile(r"^(?:(\d+)OT|OT)?$")
BOXSCORE_PATTERN = re.compile(r"^/boxscores/(\d{9}[A-Z0-9]{3})\.html$")
EXPLICIT_NEUTRAL_REMARKS = frozenset({"neutral site"})
VALID_LEAGUES = frozenset({"BAA", "NBA"})


class RegexPolicy(NamedTuple):
    """Immutable, serializable regular-expression policy."""

    pattern: str
    flags: int

    def compile(self) -> re.Pattern[str]:
        return re.compile(self.pattern, self.flags)


class BrefSchedulePolicy(NamedTuple):
    """Recursively immutable Basketball-Reference parser policy."""

    required_stats: frozenset[str]
    overtime_pattern: RegexPolicy
    boxscore_pattern: RegexPolicy
    explicit_neutral_remarks: frozenset[str]
    valid_leagues: frozenset[str]
    base_url: str


def capture_policy() -> BrefSchedulePolicy:
    """Snapshot the current parser policy without mutable aliases."""
    return BrefSchedulePolicy(
        required_stats=frozenset(str(stat) for stat in REQUIRED_STATS),
        overtime_pattern=RegexPolicy(
            pattern=str(OVERTIME_PATTERN.pattern),
            flags=int(OVERTIME_PATTERN.flags),
        ),
        boxscore_pattern=RegexPolicy(
            pattern=str(BOXSCORE_PATTERN.pattern),
            flags=int(BOXSCORE_PATTERN.flags),
        ),
        explicit_neutral_remarks=frozenset(
            str(remark) for remark in EXPLICIT_NEUTRAL_REMARKS
        ),
        valid_leagues=frozenset(str(league) for league in VALID_LEAGUES),
        base_url=str(BASE_URL),
    )


def _policy_or_current(
    policy: BrefSchedulePolicy | None,
) -> BrefSchedulePolicy:
    return capture_policy() if policy is None else policy


def _document(payload: bytes, label: str):
    try:
        return html.fromstring(payload)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{label}: invalid HTML") from exc


def _cell_text(row, stat: str) -> str:
    cells = row.xpath(f"./th[@data-stat='{stat}'] | ./td[@data-stat='{stat}']")
    if not cells:
        return ""
    return " ".join("".join(cells[0].itertext()).split())


def _cell_href(row, stat: str) -> str | None:
    hrefs = row.xpath(
        f"./th[@data-stat='{stat}']//a/@href | "
        f"./td[@data-stat='{stat}']//a/@href"
    )
    return str(hrefs[0]) if hrefs else None


def overtime_periods(
    marker: str,
    *,
    policy: BrefSchedulePolicy | None = None,
) -> int:
    policy = _policy_or_current(policy)
    normalized = marker.strip().upper()
    match = policy.overtime_pattern.compile().fullmatch(normalized)
    if match is None:
        raise ValueError(f"unsupported overtime marker: {marker!r}")
    if not normalized:
        return 0
    return int(match.group(1) or 1)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _attendance(value: str) -> tuple[int | None, str]:
    if not value.strip():
        return None, "missing"
    parsed = int(value.replace(",", ""))
    return parsed, "reported_zero" if parsed == 0 else "reported_positive"


def parse_schedule_page(
    payload: bytes,
    *,
    season: int,
    league: str,
    schedule_url: str,
    policy: BrefSchedulePolicy | None = None,
) -> pd.DataFrame:
    policy = _policy_or_current(policy)
    if league not in policy.valid_leagues:
        raise ValueError(f"unsupported league: {league}")
    root = _document(payload, schedule_url)
    tables = root.xpath("//table[@id='schedule']")
    if len(tables) != 1:
        raise ValueError(
            f"{schedule_url}: expected one schedule table, found {len(tables)}"
        )
    stats = set(tables[0].xpath(".//*[@data-stat]/@data-stat"))
    missing = sorted(policy.required_stats - stats)
    if missing:
        raise ValueError(f"{schedule_url}: missing schedule stats {missing}")
    boxscore_pattern = policy.boxscore_pattern.compile()
    records = []
    for source_row, row in enumerate(tables[0].xpath("./tbody/tr"), start=1):
        away_pts = _cell_text(row, "visitor_pts")
        home_pts = _cell_text(row, "home_pts")
        if not away_pts and not home_pts:
            continue
        box_href = _cell_href(row, "box_score_text")
        box_match = None
        if box_href is not None:
            box_match = boxscore_pattern.fullmatch(box_href)
            if box_match is None:
                raise ValueError(
                    f"{schedule_url}: source row {source_row} has invalid "
                    f"box-score href {box_href!r}"
                )
        attendance, attendance_status = _attendance(_cell_text(row, "attendance"))
        remarks = _cell_text(row, "game_remarks")
        normalized_remarks = remarks.casefold()
        site_status = (
            "home" if not remarks else (
                "neutral"
                if normalized_remarks in policy.explicit_neutral_remarks
                else "unknown"
            )
        )
        records.append({
            "season": season,
            "league": league,
            "game_date": pd.to_datetime(
                _cell_text(row, "date_game"), errors="raise"
            ).date(),
            "away_team_name": _cell_text(row, "visitor_team_name"),
            "home_team_name": _cell_text(row, "home_team_name"),
            "away_pts": int(away_pts),
            "home_pts": int(home_pts),
            "overtime_marker": _cell_text(row, "overtimes"),
            "overtime_periods": overtime_periods(
                _cell_text(row, "overtimes"), policy=policy
            ),
            "site_status": site_status,
            "venue_name": _cell_text(row, "arena_name") or pd.NA,
            "attendance": attendance,
            "attendance_status": attendance_status,
            "boxscore_url": (
                urljoin(policy.base_url, box_href)
                if box_match else pd.NA
            ),
            "boxscore_slug": box_match.group(1) if box_match else pd.NA,
            "schedule_url": schedule_url,
            "game_remarks": remarks,
            "source_row": source_row,
        })
    return assign_game_ids(pd.DataFrame(records))


BASE_URL = "https://www.basketball-reference.com"


def assign_game_ids(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.sort_values(
        ["game_date", "schedule_url", "source_row"], kind="stable"
    ).reset_index(drop=True)
    ordinals = (
        frame.groupby(
            ["game_date", "away_team_name", "home_team_name"], sort=False
        ).cumcount().add(1)
    )
    fallback_ids = [
        (
            f"bref-fallback-{date:%Y%m%d}-{_slug(away)}"
            f"-at-{_slug(home)}-{int(ordinal):02d}"
        )
        for date, away, home, ordinal in zip(
            frame["game_date"], frame["away_team_name"],
            frame["home_team_name"], ordinals, strict=True,
        )
    ]
    frame["game_id"] = pd.Series(fallback_ids, dtype="string")
    has_boxscore = frame["boxscore_slug"].notna()
    frame.loc[has_boxscore, "game_id"] = (
        "bref-" + frame.loc[has_boxscore, "boxscore_slug"].astype("string")
    )
    for column in (
        "league", "away_team_name", "home_team_name", "overtime_marker",
        "site_status", "venue_name", "attendance_status", "boxscore_url",
        "schedule_url", "game_remarks",
    ):
        frame[column] = frame[column].astype("string")
    frame["attendance"] = frame["attendance"].astype("Int64")
    return frame
