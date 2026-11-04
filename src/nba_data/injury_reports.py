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
flag), the fetch-time PDF inspection, raw PDF paths and sidecars, the
control-file reference, the immutable inventory generations and the paced,
control-bracketed acquisition itself (``fetch``).  Parsing of listing rows
lives in ``injury_report_parse``.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import time
import zoneinfo
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping

import pdfplumber
import requests

from nba_data import cache
from nba_data.stats_nba import SourceAccessError


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
ACCESS_DENIED_MARKER = b"<Code>AccessDenied</Code>"
GENERATION_PATTERN = re.compile(r"^gen-(\d{4,})\.json$")
_GENERATION_FILE_PATTERN = re.compile(r"^gen-(\d{4,})\.json(?:\.meta\.json)?(?:\.part)?$")
_LOGGER = logging.getLogger(__name__)
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


def validate_month(month: str) -> str:
    """Return ``month`` when it is ``YYYY-MM`` inside the window, else raise."""
    _parse_month(month)
    return month


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


# --------------------------------------------------------------------------
# inventory generations
# --------------------------------------------------------------------------


_INVENTORY_KEYS = {"schema_version", "month", "generation", "slots", "controls"}
def inventory_dir(data_dir: Path, month: str) -> Path:
    return Path(data_dir) / RAW_SUBDIR / "inventory" / month


def generation_number(path: Path) -> int:
    """Read the generation number of ``gen-NNNN.json`` (``gen-0007.json`` -> 7)."""
    match = GENERATION_PATTERN.match(Path(path).name)
    if match is None:
        raise InjuryReportError(
            f"not an inventory generation body name: {Path(path).name!r}"
        )
    return int(match.group(1))


def _validate_inventory_document(document: object, *, month: str, label: str) -> None:
    if not isinstance(document, dict) or set(document) != _INVENTORY_KEYS:
        raise InjuryReportError(
            f"inventory generation must carry exactly {sorted(_INVENTORY_KEYS)}: {label}"
        )
    if document["schema_version"] != 1:
        raise InjuryReportError(
            f"unsupported inventory schema_version "
            f"{document['schema_version']!r}: {label}"
        )
    if document["month"] != month:
        raise InjuryReportError(
            f"inventory generation names month {document['month']!r}, "
            f"expected {month!r}: {label}"
        )
    number = document["generation"]
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise InjuryReportError(
            f"inventory generation number must be a positive integer: {label}"
        )
    if not isinstance(document["slots"], dict):
        raise InjuryReportError(f"inventory slots must be an object: {label}")
    if not isinstance(document["controls"], list):
        raise InjuryReportError(f"inventory controls must be a list: {label}")


def _inventory_validator(month: str) -> Callable[[Path], None]:
    def validate(path: Path) -> None:
        try:
            document = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InjuryReportError(
                f"inventory generation is not JSON: {path}: {exc}"
            ) from exc
        _validate_inventory_document(document, month=month, label=str(path))

    return validate


def _existing_generation_numbers(directory: Path) -> dict[int, Path]:
    """Map every generation number a file of ``directory`` claims to its body path."""
    numbers: dict[int, Path] = {}
    if not directory.is_dir():
        return numbers
    for child in directory.iterdir():
        match = _GENERATION_FILE_PATTERN.match(child.name)
        if match is None:
            continue
        number = int(match.group(1))
        numbers.setdefault(number, directory / f"gen-{number:04d}.json")
    return numbers


def read_current_inventory(
    data_dir: Path, month: str,
) -> tuple[dict | None, tuple[Path, ...]]:
    """Return the highest verifying generation of ``month`` and the damaged ones.

    Every ``gen-NNNN.json`` body (and every orphaned sidecar) is checked in
    descending order through ``cache.read_attested``; the first that
    verifies, parses and names this month and generation is current.  Every
    other generation that fails is listed, highest number first, so a run
    can report an interrupted write without trusting it.
    """
    _parse_month(month)
    directory = inventory_dir(data_dir, month)
    current: dict | None = None
    damaged: list[Path] = []
    for number in sorted(_existing_generation_numbers(directory), reverse=True):
        path = directory / f"gen-{number:04d}.json"
        try:
            attested = cache.read_attested(path, _inventory_validator(month))
            if attested is None:
                raise cache.CacheIntegrityError(f"missing generation body {path}")
            payload, _metadata = attested
            document = json.loads(payload.decode("utf-8"))
            _validate_inventory_document(document, month=month, label=str(path))
            if document["generation"] != number:
                raise InjuryReportError(
                    f"inventory generation {path} claims number "
                    f"{document['generation']!r}"
                )
        except (cache.CacheIntegrityError, InjuryReportError, OSError,
                UnicodeDecodeError, json.JSONDecodeError):
            damaged.append(path)
            continue
        if current is None:
            current = document
    return current, tuple(damaged)


def write_inventory_generation(data_dir: Path, month: str, state: dict) -> Path:
    """Publish ``state`` as the next generation of ``month``, above every existing file.

    ``state`` carries ``slots`` and ``controls``; ``schema_version``,
    ``month`` and ``generation`` are filled in (and must agree when present).
    The body is sorted JSON published through ``cache.publish_bytes`` with
    the contracted sidecar, so the pair verifies or is visibly damaged.
    """
    _parse_month(month)
    if not isinstance(state, dict) or not {"slots", "controls"} <= set(state):
        raise InjuryReportError(
            "inventory state must carry slots and controls"
        )
    unexpected = set(state) - _INVENTORY_KEYS
    if unexpected:
        raise InjuryReportError(
            f"inventory state carries unexpected keys {sorted(unexpected)}"
        )
    directory = inventory_dir(data_dir, month)
    existing = _existing_generation_numbers(directory)
    number = (max(existing) if existing else 0) + 1
    if "generation" in state and state["generation"] != number:
        raise InjuryReportError(
            f"inventory state claims generation {state['generation']!r}, "
            f"next generation of {month} is {number}"
        )
    document = {
        **deepcopy(state),
        "schema_version": state.get("schema_version", 1),
        "month": state.get("month", month),
        "generation": number,
    }
    _validate_inventory_document(document, month=month, label=f"{month} gen-{number:04d}")
    payload = (
        json.dumps(document, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")
    return cache.publish_bytes(
        directory / f"gen-{number:04d}.json",
        payload,
        {
            "provider": "nba_official",
            "resource": "injury_report_inventory",
            "month": month,
            "generation": number,
        },
        _inventory_validator(month),
    )


# --------------------------------------------------------------------------
# transport: pacing, controls and probes
# --------------------------------------------------------------------------


@dataclass
class RequestPacer:
    """Serial pacing for the PDF host: request starts at least 0.5 s apart."""

    minimum_interval_s: float = MIN_REQUEST_INTERVAL_S
    now: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep
    last_started: float | None = None

    def __post_init__(self) -> None:
        if self.minimum_interval_s < MIN_REQUEST_INTERVAL_S:
            raise ValueError(
                "RequestPacer minimum_interval_s must be >= "
                f"{MIN_REQUEST_INTERVAL_S}: {self.minimum_interval_s}"
            )

    def wait(self) -> None:
        current = self.now()
        if self.last_started is not None:
            delay = self.last_started + self.minimum_interval_s - current
            if delay > 0:
                self.sleeper(delay)
                current = self.now()
        self.last_started = current


_TOTAL_ATTEMPTS = len(RETRY_DELAYS_S) + 1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _header(headers: Mapping[str, str] | None, name: str) -> str | None:
    if not headers:
        return None
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return value
    return None


def _get(session, url: str, *, pacer: RequestPacer):
    """One paced GET with the fixed transport contract of the PDF host."""
    pacer.wait()
    return session.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT_S,
        allow_redirects=False,
    )


def check_control(session, file_name: str, expected: dict, *, pacer) -> dict:
    """Request one control file and require its committed body, else fail closed.

    No retry: a control that does not come back as HTTP 200 with exactly the
    committed length and SHA-256 raises ``SourceAccessError`` so the caller
    discards whatever it gathered since the last good control.
    """
    checked_at = _iso_z(_utc_now())
    url = PDF_BASE_URL + file_name
    try:
        response = _get(session, url, pacer=pacer)
    except requests.RequestException as exc:
        raise SourceAccessError(
            f"control {file_name}: {type(exc).__name__}: {exc}"
        ) from exc
    status = response.status_code
    body = response.content
    if status != 200:
        raise SourceAccessError(
            f"control {file_name}: HTTP {status} ({len(body)} bytes)"
        )
    digest = hashlib.sha256(body).hexdigest()
    if len(body) != expected["bytes"] or digest != expected["sha256"]:
        raise SourceAccessError(
            f"control {file_name}: body {len(body)} bytes sha256 {digest} differs "
            f"from the committed {expected['bytes']} bytes {expected['sha256']}"
        )
    return {
        "checked_at": checked_at,
        "file_name": file_name,
        "sha256": digest,
        "ok": True,
    }


def _access_denied_evidence(response, fetched_at: datetime) -> dict | None:
    """Evidence for a qualifying S3 ``AccessDenied`` 403, else ``None``."""
    body = response.content
    if (
        response.status_code != 403
        or len(body) > MAX_ACCESS_DENIED_BYTES
        or ACCESS_DENIED_MARKER not in body
    ):
        return None
    try:
        body_text = body.decode("utf-8")
    except UnicodeDecodeError:
        return None
    headers = response.headers
    return {
        "fetched_at": _iso_z(fetched_at),
        "http_status": 403,
        "date_header": _header(headers, "Date"),
        "x_amz_request_id": _header(headers, "x-amz-request-id"),
        "x_amz_id_2": _header(headers, "x-amz-id-2"),
        "body_bytes": len(body),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "body_text": body_text,
    }


def _probe(
    session, slot: Slot, *, pacer: RequestPacer, sleeper: Callable[[float], None],
) -> tuple[str, bytes | None, dict | None]:
    """Request one slot: ``("present", body, None)`` or ``("unavailable", None, evidence)``.

    Anything but a ``%PDF`` 200 or a qualifying ``AccessDenied`` 403 is retried
    with the 5/10/20/40 s delays and then raises ``SourceAccessError``.
    """
    context = f"injury report {slot.report_id}"
    for attempt in range(1, _TOTAL_ATTEMPTS + 1):
        fetched_at = _utc_now()
        try:
            response = _get(session, slot.url, pacer=pacer)
        except requests.RequestException as exc:
            outcome = f"{type(exc).__name__}: {exc}"
        else:
            status = response.status_code
            body = response.content
            if status == 200 and body.startswith(PDF_SIGNATURE):
                return "present", body, None
            evidence = _access_denied_evidence(response, fetched_at)
            if evidence is not None:
                return "unavailable", None, evidence
            outcome = f"HTTP {status} ({len(body)} bytes)"
        if attempt == _TOTAL_ATTEMPTS:
            raise SourceAccessError(
                f"{context}: exhausted retries after {attempt} attempts "
                f"(final {outcome})"
            )
        sleeper(RETRY_DELAYS_S[attempt - 1])
    raise AssertionError("unreachable: probe retry loop exited without result")


# --------------------------------------------------------------------------
# fetch
# --------------------------------------------------------------------------


def _slot_entry(slot: Slot) -> dict:
    return {
        "grid": slot.grid,
        "slot_date": slot.slot_date.isoformat(),
        "slot_label": slot.slot_label,
        "url": slot.url,
        "status": "unavailable",
        "sha256": None,
        "bytes": None,
        "evidence": [],
    }


def _present_entry(slot: Slot, payload: bytes, evidence: list) -> dict:
    return {
        **_slot_entry(slot),
        "status": "present",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "evidence": deepcopy(evidence),
    }


def _latest_evidence_date(entry: dict, *, report_id: str) -> date:
    evidence = entry.get("evidence") or []
    if not evidence:
        raise InjuryReportError(
            f"unavailable slot {report_id} carries no evidence"
        )
    stamps = []
    for item in evidence:
        try:
            stamps.append(datetime.strptime(item["fetched_at"], "%Y-%m-%dT%H:%M:%SZ"))
        except (KeyError, TypeError, ValueError):
            raise InjuryReportError(
                f"unavailable slot {report_id} carries malformed fetched_at evidence"
            ) from None
    return max(stamps).date()


class _MonthState:
    """One month's known slots, prior controls and this run's bookkeeping."""

    def __init__(self, month: str, current: dict | None) -> None:
        self.month = month
        self.slots: dict[str, dict] = deepcopy(current["slots"]) if current else {}
        self.prior_controls: list = deepcopy(current["controls"]) if current else []
        self.dirty = False          # something to persist at the next checkpoint
        self.written = 0


def fetch(
    data_dir: Path,
    months: Iterable[str] | None = None,
    *,
    recheck_unavailable: bool = False,
    session=None,
    pacer=None,
    sleeper=time.sleep,
    today: date | None = None,
) -> dict:
    """Probe every unknown slot of ``months`` (default: the whole window) under controls.

    The known state of a month is its current inventory generation merged
    with every PDF pair on disk (a pair published by an interrupted run is
    ``present`` from its own sidecar and never re-requested).  Unknown slots
    are probed in ``enumerate_slots`` order; with ``recheck_unavailable`` the
    ``unavailable`` slots whose latest evidence is dated (UTC) before
    ``today`` are requested once more afterwards, keeping the earlier
    evidence as history, while slots already answered today are counted as
    ``skipped_today``.  The three control files are checked at the start,
    after every ``CONTROL_INTERVAL`` requests and as a closing control; only
    after a successful control is one generation written per month that has
    something new, so a failed control discards the in-memory batch (PDF
    pairs already published stay on disk).  Returns ``{"probed", "present",
    "unavailable", "rechecked", "skipped_today", "generations_written"}``,
    where ``probed`` counts every slot request of the run including rechecks.

    TODO(task-19): the real acquisition of the 90,912 slots (about 13 hours
    of paced requests) and the later-day ``--recheck-unavailable`` pass run
    on the owner's machine in plan Task 19; nothing under
    ``data/raw/nba_injury_reports/`` exists in this checkout.
    """
    data_dir = Path(data_dir)
    today = today if today is not None else _utc_now().date()
    slots = enumerate_slots(months)
    controls = load_controls()

    states: dict[str, _MonthState] = {}
    probes: list[Slot] = []
    rechecks: list[Slot] = []
    skipped_today = 0
    for slot in slots:
        state = states.get(slot.month)
        if state is None:
            current, damaged = read_current_inventory(data_dir, slot.month)
            for path in damaged:
                _LOGGER.warning(
                    "ignoring unverifiable injury-report inventory generation %s", path,
                )
            state = states[slot.month] = _MonthState(slot.month, current)
        known = state.slots.get(slot.report_id)
        attested = cache.read_attested(pdf_path(data_dir, slot), validate_pdf)
        if attested is not None:
            payload, _metadata = attested
            entry = _present_entry(
                slot, payload, known["evidence"] if known else [],
            )
            if (
                known is not None
                and known["status"] == "present"
                and (known["sha256"], known["bytes"]) != (entry["sha256"], entry["bytes"])
            ):
                raise InjuryReportError(
                    f"inventory {slot.month} records {slot.report_id} as present "
                    f"with sha256 {known['sha256']} ({known['bytes']} bytes) but the "
                    f"PDF pair on disk is sha256 {entry['sha256']} ({entry['bytes']} bytes)"
                )
            if entry != known:
                state.slots[slot.report_id] = entry
                state.dirty = True
            continue
        if known is None:
            probes.append(slot)
            continue
        if known["status"] == "present":
            raise InjuryReportError(
                f"inventory {slot.month} records {slot.report_id} as present "
                f"but no PDF pair exists at {pdf_path(data_dir, slot)}"
            )
        if known["status"] != "unavailable":
            raise InjuryReportError(
                f"inventory {slot.month} carries unknown status "
                f"{known['status']!r} for {slot.report_id}"
            )
        if recheck_unavailable:
            if _latest_evidence_date(known, report_id=slot.report_id) < today:
                rechecks.append(slot)
            else:
                skipped_today += 1

    summary = {
        "probed": 0,
        "present": 0,
        "unavailable": 0,
        "rechecked": 0,
        "skipped_today": skipped_today,
        "generations_written": 0,
    }
    work = [(slot, False) for slot in probes] + [(slot, True) for slot in rechecks]
    if not work and not any(state.dirty for state in states.values()):
        return summary

    session = session if session is not None else requests.Session()
    pacer = pacer if pacer is not None else RequestPacer()
    run_controls: list[dict] = []
    touched = [
        month for month in states
        if states[month].dirty or any(slot.month == month for slot, _ in work)
    ]

    def checkpoint() -> None:
        """Check the three controls, then persist every touched month with news."""
        for file_name, expected in controls.items():
            run_controls.append(
                check_control(session, file_name, expected, pacer=pacer)
            )
        for month in touched:
            state = states[month]
            if state.written and not state.dirty:
                continue
            write_inventory_generation(
                data_dir,
                month,
                {
                    "slots": state.slots,
                    "controls": state.prior_controls + run_controls,
                },
            )
            state.dirty = False
            state.written += 1
            summary["generations_written"] += 1

    checkpoint()
    since_control = 0
    for slot, is_recheck in work:
        status, payload, evidence = _probe(
            session, slot, pacer=pacer, sleeper=sleeper,
        )
        state = states[slot.month]
        known = state.slots.get(slot.report_id)
        history = known["evidence"] if known else []
        if status == "present":
            cache.publish_bytes(
                pdf_path(data_dir, slot),
                payload,
                pdf_metadata(slot, inspect_pdf(payload)),
                validate_pdf,
            )
            state.slots[slot.report_id] = _present_entry(slot, payload, history)
            summary["present"] += 1
        else:
            entry = _slot_entry(slot)
            entry["evidence"] = deepcopy(history) + [evidence]
            state.slots[slot.report_id] = entry
            summary["unavailable"] += 1
        state.dirty = True
        summary["probed"] += 1
        if is_recheck:
            summary["rechecked"] += 1
        since_control += 1
        if since_control == CONTROL_INTERVAL:
            checkpoint()
            since_control = 0
    if since_control:
        checkpoint()
    return summary
