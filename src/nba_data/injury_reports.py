"""Official NBA injury-report PDFs: slot grids, titles and acquisition (Part B).

Reports are served from a public static host under file names that encode
their publication slot in US Eastern 12-hour time.  Two grids are
enumerated: ``hour`` (24 slots a day, 2018-12-17 through 2025-12-31) and
``quarter`` (96 slots a day, 2025-12-01 through 2026-09-30).  The grids
overlap for December 2025 so the cadence switch is observed rather than
assumed; ``Injury-Report_2025-12-22_05PM`` and
``Injury-Report_2025-12-22_05_00PM`` are distinct slots and both are probed.

This module owns the slot arithmetic, the printed-title rules (the slot
window, the Eastern-to-UTC conversion with its daylight-saving ambiguity
flag), the fetch-time PDF inspection, raw PDF paths and sidecars, and the
control-file reference.  Parsing of listing rows lives in
``injury_report_parse``.
"""

from __future__ import annotations

import io
import json
import re
import zoneinfo
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import pdfplumber


PDF_BASE_URL = "https://ak-static.cms.nba.com/referee/injury/"
RAW_SUBDIR = "raw/nba_injury_reports"
GRIDS = {
    "hour": (date(2018, 12, 17), date(2025, 12, 31), 60),
    "quarter": (date(2025, 12, 1), date(2026, 9, 30), 15),
}
WINDOW = (date(2018, 12, 17), date(2026, 9, 30))
USER_AGENT = "nba-predictions-revived availability acquisition"
MIN_REQUEST_INTERVAL_S = 0.5
TIMEOUT_S = 60
RETRY_DELAYS_S = (5.0, 10.0, 20.0, 40.0)
CONTROL_INTERVAL = 200
MAX_ACCESS_DENIED_BYTES = 1024
CONTROLS_PATH = Path(__file__).resolve().parent / "reference/injury_report_controls.json"
TITLE_PATTERN = re.compile(
    r"Injury Report:\s*(\d{2})/(\d{2})/(\d{2})\s+(\d{2}):(\d{2})\s*(AM|PM)"
)
EASTERN = zoneinfo.ZoneInfo("America/New_York")

PDF_SIGNATURE = b"%PDF"
REPORT_ID_PATTERN = re.compile(
    r"^Injury-Report_(\d{4})-(\d{2})-(\d{2})_(\d{2})(?:_(\d{2}))?(AM|PM)$"
)
MONTH_PATTERN = re.compile(r"^(\d{4})-(\d{2})$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CONTROL_FIELDS = {"bytes", "sha256"}
_X_TOLERANCE = 1.5


class InjuryReportError(ValueError):
    """Raised when an injury-report slot, title, body or reference violates its contract."""


@dataclass(frozen=True)
class Slot:
    grid: str
    slot_date: date
    slot_label: str
    slot_start_et: datetime
    report_id: str
    file_name: str
    url: str
    month: str


@dataclass(frozen=True)
class PdfInspection:
    opens: bool
    pages: int | None
    title_text: str | None
    title_printed_et: datetime | None


# --------------------------------------------------------------------------
# slot grids and file names
# --------------------------------------------------------------------------


def _grid_minutes(grid: str) -> int:
    if grid not in GRIDS:
        raise InjuryReportError(f"unknown injury-report grid: {grid!r}")
    return GRIDS[grid][2]


def slot_label(grid: str, hour: int, minute: int) -> str:
    """Render the file-name time token (``05PM`` or ``05_30PM``) of one slot."""
    minutes = _grid_minutes(grid)
    if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour < 24:
        raise InjuryReportError(f"slot hour outside 0..23: {hour!r}")
    if (
        isinstance(minute, bool)
        or not isinstance(minute, int)
        or not 0 <= minute < 60
        or minute % minutes
    ):
        raise InjuryReportError(
            f"slot minute {minute!r} is not on the {grid} grid"
        )
    twelve_hour = hour % 12 or 12
    meridiem = "AM" if hour < 12 else "PM"
    if grid == "hour":
        return f"{twelve_hour:02d}{meridiem}"
    return f"{twelve_hour:02d}_{minute:02d}{meridiem}"


def _slot(grid: str, slot_date: date, hour: int, minute: int) -> Slot:
    label = slot_label(grid, hour, minute)
    report_id = f"Injury-Report_{slot_date.isoformat()}_{label}"
    file_name = report_id + ".pdf"
    return Slot(
        grid=grid,
        slot_date=slot_date,
        slot_label=label,
        slot_start_et=datetime(
            slot_date.year, slot_date.month, slot_date.day, hour, minute
        ),
        report_id=report_id,
        file_name=file_name,
        url=PDF_BASE_URL + file_name,
        month=f"{slot_date.year:04d}-{slot_date.month:02d}",
    )


def _slot_sort_key(slot: Slot) -> tuple[date, datetime, str]:
    return (slot.slot_date, slot.slot_start_et, slot.grid)


def _parse_month(month: object) -> tuple[int, int]:
    match = MONTH_PATTERN.match(month) if isinstance(month, str) else None
    if match is None:
        raise InjuryReportError(f"month must be YYYY-MM: {month!r}")
    year, number = int(match.group(1)), int(match.group(2))
    if not 1 <= number <= 12:
        raise InjuryReportError(f"month must be YYYY-MM: {month!r}")
    first, last = WINDOW
    if (year, number) < (first.year, first.month) or (
        (year, number) > (last.year, last.month)
    ):
        raise InjuryReportError(
            f"month {month} lies outside the injury-report window "
            f"{first.isoformat()}..{last.isoformat()}"
        )
    return year, number


def enumerate_slots(months: Iterable[str] | None = None) -> tuple[Slot, ...]:
    """Enumerate every slot of both grids, optionally restricted to months.

    Every calendar date of each grid's range is enumerated, including
    off-season dates; absence is observed at fetch time, never presumed.
    The result is sorted by ``(slot_date, slot_start_et, grid)`` and holds
    no duplicate ``report_id``.
    """
    selected: set[tuple[int, int]] | None = None
    if months is not None:
        selected = {_parse_month(month) for month in months}
    slots: list[Slot] = []
    for grid, (first, last, minutes) in GRIDS.items():
        per_day = [
            (start // 60, start % 60) for start in range(0, 24 * 60, minutes)
        ]
        current = first
        while current <= last:
            if selected is None or (current.year, current.month) in selected:
                for hour, minute in per_day:
                    slots.append(_slot(grid, current, hour, minute))
            current += timedelta(days=1)
    slots.sort(key=_slot_sort_key)
    report_ids = [slot.report_id for slot in slots]
    if len(set(report_ids)) != len(report_ids):
        raise InjuryReportError("slot enumeration produced a duplicate report_id")
    return tuple(slots)


def slot_from_report_id(report_id: str) -> Slot:
    """Rebuild the ``Slot`` a file name (without ``.pdf``) encodes."""
    match = REPORT_ID_PATTERN.match(report_id) if isinstance(report_id, str) else None
    if match is None:
        raise InjuryReportError(f"malformed injury-report id: {report_id!r}")
    year, month, day, hour_token, minute_token, meridiem = match.groups()
    try:
        slot_date = date(int(year), int(month), int(day))
    except ValueError:
        raise InjuryReportError(
            f"injury-report id carries an impossible date: {report_id!r}"
        ) from None
    twelve_hour = int(hour_token)
    if not 1 <= twelve_hour <= 12:
        raise InjuryReportError(
            f"injury-report id hour must be 12 or 01..11: {report_id!r}"
        )
    hour = twelve_hour % 12 + (12 if meridiem == "PM" else 0)
    minute = int(minute_token) if minute_token is not None else 0
    grid = "hour" if minute_token is None else "quarter"
    first, last, _minutes = GRIDS[grid]
    if not first <= slot_date <= last:
        raise InjuryReportError(
            f"injury-report id lies outside the {grid} grid "
            f"{first.isoformat()}..{last.isoformat()}: {report_id!r}"
        )
    try:
        slot = _slot(grid, slot_date, hour, minute)
    except InjuryReportError:
        raise InjuryReportError(
            f"injury-report id is not on the {grid} grid: {report_id!r}"
        ) from None
    if slot.report_id != report_id:
        raise InjuryReportError(
            f"injury-report id does not round-trip: {report_id!r}"
        )
    return slot


# --------------------------------------------------------------------------
# printed titles, the slot window and Eastern time
# --------------------------------------------------------------------------


def parse_title(text: str) -> datetime | None:
    """Read the naive Eastern time printed in ``Injury Report: MM/DD/YY hh:mm AM``."""
    match = TITLE_PATTERN.search(text or "")
    if match is None:
        return None
    month, day, year, hour_token, minute, meridiem = match.groups()
    twelve_hour = int(hour_token)
    if not 1 <= twelve_hour <= 12:
        return None
    hour = twelve_hour % 12 + (12 if meridiem == "PM" else 0)
    try:
        return datetime(
            2000 + int(year), int(month), int(day), hour, int(minute)
        )
    except ValueError:
        return None


def title_check(slot: Slot, printed: datetime | None) -> str:
    """Classify a printed title against its slot: ``ok``, ``failed`` or ``unparsed``.

    The league prints the generation time, so the rule is a window rather
    than equality: the slot's clock hour for the ``hour`` grid and the
    fifteen minutes from the slot start for the ``quarter`` grid.
    """
    if printed is None:
        return "unparsed"
    window = timedelta(minutes=_grid_minutes(slot.grid))
    if (
        printed.date() == slot.slot_date
        and slot.slot_start_et <= printed < slot.slot_start_et + window
    ):
        return "ok"
    return "failed"


def printed_to_utc(printed_et: datetime) -> tuple[datetime | None, bool]:
    """Localise a naive printed Eastern time to UTC.

    Returns ``(aware UTC, False)`` for an unambiguous wall time and
    ``(None, True)`` for the repeated 1 AM hour of the November switch day,
    where the printed time names two instants one hour apart.  A wall time
    inside the skipped 2 AM hour of the March switch day never happened
    and raises.
    """
    if printed_et.tzinfo is not None:
        raise InjuryReportError(
            f"printed Eastern time must be naive: {printed_et.isoformat()}"
        )
    candidates = []
    for fold in (0, 1):
        aware = printed_et.replace(tzinfo=EASTERN, fold=fold)
        as_utc = aware.astimezone(timezone.utc)
        round_trip = as_utc.astimezone(EASTERN).replace(tzinfo=None)
        if round_trip != printed_et:
            raise InjuryReportError(
                "printed Eastern time falls in the skipped daylight-saving hour: "
                f"{printed_et.isoformat()}"
            )
        candidates.append(as_utc)
    if candidates[0] != candidates[1]:
        return None, True
    return candidates[0], False


# --------------------------------------------------------------------------
# PDF inspection, raw paths and sidecars
# --------------------------------------------------------------------------


def inspect_pdf(payload: bytes) -> PdfInspection:
    """Record whether a body opens and what title its first page prints."""
    try:
        with pdfplumber.open(io.BytesIO(payload)) as pdf:
            pages = len(pdf.pages)
            if pages < 1:
                return PdfInspection(
                    opens=False, pages=None, title_text=None, title_printed_et=None,
                )
            words = pdf.pages[0].extract_words(x_tolerance=_X_TOLERANCE)
            text = " ".join(str(word["text"]) for word in words)
    except Exception:
        return PdfInspection(
            opens=False, pages=None, title_text=None, title_printed_et=None,
        )
    match = TITLE_PATTERN.search(text)
    title_text = match.group(0) if match is not None else None
    return PdfInspection(
        opens=True,
        pages=pages,
        title_text=title_text,
        title_printed_et=parse_title(text),
    )


def pdf_path(data_dir: Path, slot: Slot) -> Path:
    return Path(data_dir) / RAW_SUBDIR / "pdfs" / slot.month / slot.file_name


def pdf_metadata(slot: Slot, inspection: PdfInspection) -> dict:
    """The sidecar identity of one archived PDF body."""
    printed = inspection.title_printed_et
    return {
        "provider": "nba_official",
        "resource": "injury_report",
        "url": slot.url,
        "grid": slot.grid,
        "slot_date": slot.slot_date.isoformat(),
        "slot_label": slot.slot_label,
        "report_id": slot.report_id,
        "opens": inspection.opens,
        "pages": inspection.pages,
        "title_printed_et": printed.isoformat() if printed is not None else None,
        "title_check": title_check(slot, printed),
    }


def validate_pdf(path: Path) -> None:
    """Accept a body only when it begins with the ``%PDF`` signature."""
    try:
        with Path(path).open("rb") as stream:
            head = stream.read(len(PDF_SIGNATURE))
    except OSError as exc:
        raise InjuryReportError(f"cannot read PDF body {path}: {exc}") from exc
    if head != PDF_SIGNATURE:
        raise InjuryReportError(f"body does not begin with %PDF: {path}")


# --------------------------------------------------------------------------
# control files
# --------------------------------------------------------------------------


def load_controls(path: Path = CONTROLS_PATH) -> dict[str, dict]:
    """Load the committed control files (``file_name`` -> ``{bytes, sha256}``)."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InjuryReportError(f"cannot read controls file {path}: {exc}") from exc
    if not isinstance(document, dict) or set(document) != {
        "schema_version", "controls",
    }:
        raise InjuryReportError(
            f"controls file must carry exactly schema_version and controls: {path}"
        )
    if document["schema_version"] != 1:
        raise InjuryReportError(
            f"unsupported controls schema_version {document['schema_version']!r}: {path}"
        )
    controls = document["controls"]
    if not isinstance(controls, dict) or not controls:
        raise InjuryReportError(
            f"controls file must carry a non-empty controls object: {path}"
        )
    for file_name, entry in controls.items():
        if not isinstance(file_name, str) or not file_name.endswith(".pdf"):
            raise InjuryReportError(
                f"control key must be a report file name: {file_name!r}"
            )
        slot = slot_from_report_id(file_name[: -len(".pdf")])
        if slot.file_name != file_name:
            raise InjuryReportError(
                f"control key does not name a slot file: {file_name!r}"
            )
        if not isinstance(entry, dict) or set(entry) != _CONTROL_FIELDS:
            raise InjuryReportError(
                f"control {file_name} must carry exactly bytes and sha256"
            )
        size = entry["bytes"]
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise InjuryReportError(
                f"control {file_name} must carry a positive byte count"
            )
        digest = entry["sha256"]
        if not isinstance(digest, str) or not _SHA256_PATTERN.match(digest):
            raise InjuryReportError(
                f"control {file_name} must carry a lowercase SHA-256 digest"
            )
    return deepcopy(controls)
