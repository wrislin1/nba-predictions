"""Tests for official injury-report slot grids, titles and PDF inspection."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

from nba_data import cache, injury_reports


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/injury_reports"
SOURCES = json.loads((FIXTURES / "SOURCES.json").read_text(encoding="utf-8"))
FIXTURE_TITLES = {
    "Injury-Report_2018-12-17_05PM.pdf": datetime(2018, 12, 17, 17, 30),
    "Injury-Report_2019-02-15_01PM.pdf": datetime(2019, 2, 15, 13, 30),
    "Injury-Report_2019-09-30_01PM.pdf": datetime(2019, 9, 30, 13, 30),
    "Injury-Report_2021-04-01_05PM.pdf": datetime(2021, 4, 1, 17, 30),
    "Injury-Report_2024-01-31_05PM.pdf": datetime(2024, 1, 31, 17, 30),
    "Injury-Report_2026-01-10_12_00AM.pdf": datetime(2026, 1, 10, 0, 0),
}


# --------------------------------------------------------------------------
# fixtures and packaged reference data
# --------------------------------------------------------------------------


def test_fixture_pdfs_match_sources_json() -> None:
    """Would fail if a committed fixture drifted from its recorded identity."""
    assert set(SOURCES) == set(FIXTURE_TITLES)
    assert sorted(path.name for path in FIXTURES.glob("*.pdf")) == sorted(SOURCES)
    for name, record in SOURCES.items():
        payload = (FIXTURES / name).read_bytes()
        assert set(record) == {"url", "sha256", "bytes", "pages", "retrieved"}
        assert record["url"] == injury_reports.PDF_BASE_URL + name
        assert record["sha256"] == hashlib.sha256(payload).hexdigest()
        assert record["bytes"] == len(payload)
        assert isinstance(record["pages"], int) and record["pages"] >= 1
        assert record["retrieved"] == "2026-11-04"


def test_controls_file_is_canonical_json() -> None:
    payload = injury_reports.CONTROLS_PATH.read_bytes()
    document = json.loads(payload)
    canonical = (
        json.dumps(document, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")
    assert payload == canonical
    assert document["schema_version"] == 1


# --------------------------------------------------------------------------
# slot grids
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("grid", "hour", "minute", "expected"),
    [
        ("hour", 0, 0, "12AM"),
        ("hour", 1, 0, "01AM"),
        ("hour", 11, 0, "11AM"),
        ("hour", 12, 0, "12PM"),
        ("hour", 13, 0, "01PM"),
        ("hour", 23, 0, "11PM"),
        ("quarter", 0, 0, "12_00AM"),
        ("quarter", 0, 15, "12_15AM"),
        ("quarter", 12, 45, "12_45PM"),
        ("quarter", 17, 30, "05_30PM"),
        ("quarter", 23, 45, "11_45PM"),
    ],
)
def test_slot_label_renders_both_grids(grid, hour, minute, expected) -> None:
    assert injury_reports.slot_label(grid, hour, minute) == expected


@pytest.mark.parametrize(
    ("grid", "hour", "minute"),
    [
        ("hour", 13, 30),
        ("quarter", 13, 10),
        ("quarter", 24, 0),
        ("hour", -1, 0),
        ("daily", 13, 0),
    ],
)
def test_slot_label_rejects_times_off_the_grid(grid, hour, minute) -> None:
    with pytest.raises(injury_reports.InjuryReportError):
        injury_reports.slot_label(grid, hour, minute)


def test_enumerate_slots_counts_90912_with_both_grids_in_december_2025_and_no_duplicate_report_ids() -> None:
    """Would fail if the overlapping December 2025 grids collapsed or repeated."""
    slots = injury_reports.enumerate_slots()

    assert len(slots) == 90912
    report_ids = [slot.report_id for slot in slots]
    assert len(set(report_ids)) == len(report_ids)
    assert len({slot.file_name for slot in slots}) == len(slots)
    assert len({slot.url for slot in slots}) == len(slots)

    december = [slot for slot in slots if slot.month == "2025-12"]
    assert len(december) == 31 * 24 + 31 * 96
    assert sum(slot.grid == "hour" for slot in december) == 31 * 24
    assert sum(slot.grid == "quarter" for slot in december) == 31 * 96
    assert {"Injury-Report_2025-12-22_05PM", "Injury-Report_2025-12-22_05_00PM"} <= set(report_ids)

    by_grid = {}
    for slot in slots:
        by_grid.setdefault(slot.grid, []).append(slot)
    assert set(by_grid) == set(injury_reports.GRIDS)
    for grid, (first, last, minutes) in injury_reports.GRIDS.items():
        dates = sorted({slot.slot_date for slot in by_grid[grid]})
        assert dates[0] == first
        assert dates[-1] == last
        assert len(dates) == (last - first).days + 1
        assert len(by_grid[grid]) == len(dates) * (24 * 60 // minutes)
    assert min(slot.slot_date for slot in slots) == injury_reports.WINDOW[0]
    assert max(slot.slot_date for slot in slots) == injury_reports.WINDOW[1]
    assert slots == tuple(
        sorted(slots, key=lambda slot: (slot.slot_date, slot.slot_start_et, slot.grid))
    )


def test_enumerate_slots_month_filter_and_ordering() -> None:
    slots = injury_reports.enumerate_slots(months=["2024-01"])

    assert len(slots) == 744
    assert all(slot.grid == "hour" for slot in slots)
    assert all(slot.month == "2024-01" for slot in slots)
    assert [slot.slot_start_et for slot in slots] == sorted(
        slot.slot_start_et for slot in slots
    )
    assert slots == tuple(
        sorted(slots, key=lambda slot: (slot.slot_date, slot.slot_start_et, slot.grid))
    )

    first = slots[0]
    assert first == injury_reports.Slot(
        grid="hour",
        slot_date=date(2024, 1, 1),
        slot_label="12AM",
        slot_start_et=datetime(2024, 1, 1, 0, 0),
        report_id="Injury-Report_2024-01-01_12AM",
        file_name="Injury-Report_2024-01-01_12AM.pdf",
        url="https://ak-static.cms.nba.com/referee/injury/Injury-Report_2024-01-01_12AM.pdf",
        month="2024-01",
    )
    assert first.slot_start_et.tzinfo is None
    last = slots[-1]
    assert last.report_id == "Injury-Report_2024-01-31_11PM"
    assert last.slot_start_et == datetime(2024, 1, 31, 23, 0)
    assert last.file_name == last.report_id + ".pdf"
    assert last.url == injury_reports.PDF_BASE_URL + last.file_name

    quarter = injury_reports.enumerate_slots(months=["2026-09"])
    assert len(quarter) == 30 * 96
    assert quarter[1].slot_label == "12_15AM"
    assert quarter[1].slot_start_et == datetime(2026, 9, 1, 0, 15)
    assert quarter[-1].report_id == "Injury-Report_2026-09-30_11_45PM"

    both = injury_reports.enumerate_slots(months=["2025-12", "2024-01"])
    assert len(both) == 744 + 31 * 24 + 31 * 96
    assert both[0].month == "2024-01"
    assert both[-1].month == "2025-12"
    overlapping = [
        slot for slot in both
        if slot.slot_start_et == datetime(2025, 12, 22, 17, 0)
    ]
    assert [slot.grid for slot in overlapping] == ["hour", "quarter"]

    # Repeating a month must not repeat its slots.
    assert injury_reports.enumerate_slots(months=["2024-01", "2024-01"]) == slots
    assert injury_reports.enumerate_slots(months=[]) == ()


@pytest.mark.parametrize("month", ["2018-11", "2026-10", "2024-1", "202401", "January"])
def test_enumerate_slots_rejects_months_outside_the_window(month) -> None:
    with pytest.raises(injury_reports.InjuryReportError):
        injury_reports.enumerate_slots(months=[month])


def test_slot_from_report_id_round_trips() -> None:
    slots = injury_reports.enumerate_slots(months=["2025-12", "2026-01"])
    for slot in slots:
        assert injury_reports.slot_from_report_id(slot.report_id) == slot

    slot = injury_reports.slot_from_report_id("Injury-Report_2024-01-31_05PM")
    assert slot.grid == "hour"
    assert slot.slot_date == date(2024, 1, 31)
    assert slot.slot_label == "05PM"
    assert slot.slot_start_et == datetime(2024, 1, 31, 17, 0)
    assert slot.file_name == "Injury-Report_2024-01-31_05PM.pdf"
    assert slot.month == "2024-01"

    quarter = injury_reports.slot_from_report_id("Injury-Report_2026-01-10_12_00AM")
    assert quarter.grid == "quarter"
    assert quarter.slot_start_et == datetime(2026, 1, 10, 0, 0)
    assert quarter.slot_label == "12_00AM"


@pytest.mark.parametrize(
    "report_id",
    [
        "Injury-Report_2024-01-31_05PM.pdf",
        "Injury-Report_2024-01-31_17PM",
        "Injury-Report_2024-01-31_00AM",
        "Injury-Report_2024-01-31_5PM",
        "Injury-Report_2024-01-31_05_10PM",
        "Injury-Report_2024-01-31_05_00PM",
        "Injury-Report_2026-01-10_12AM",
        "Injury-Report_2018-12-16_05PM",
        "Injury-Report_2026-10-01_05_00PM",
        "Injury-Report_2024-02-30_05PM",
        "injury-report_2024-01-31_05PM",
        "",
    ],
)
def test_slot_from_report_id_rejects_ids_off_the_grids(report_id) -> None:
    with pytest.raises(injury_reports.InjuryReportError):
        injury_reports.slot_from_report_id(report_id)


# --------------------------------------------------------------------------
# titles, the slot window and Eastern time
# --------------------------------------------------------------------------


def test_parse_title_reads_the_printed_time() -> None:
    assert injury_reports.parse_title(
        "Injury Report: 01/31/24 05:30 PM"
    ) == datetime(2024, 1, 31, 17, 30)
    assert injury_reports.parse_title(
        "Injury Report: 01/10/26 12:00 AM Game Date Game Time"
    ) == datetime(2026, 1, 10, 0, 0)
    assert injury_reports.parse_title(
        "header Injury Report:12/17/18 12:30PM trailer"
    ) == datetime(2018, 12, 17, 12, 30)
    assert injury_reports.parse_title("garbage") is None
    assert injury_reports.parse_title("") is None
    assert injury_reports.parse_title("Injury Report: 1/31/24 05:30 PM") is None
    assert injury_reports.parse_title("Injury Report: 13/45/24 05:30 PM") is None
    assert injury_reports.parse_title("Injury Report: 01/31/24 00:30 PM") is None
    assert injury_reports.parse_title("Injury Report: 01/31/24 05:60 PM") is None


@pytest.mark.parametrize(
    ("report_id", "printed", "expected"),
    [
        ("Injury-Report_2024-01-31_01PM", datetime(2024, 1, 31, 13, 30), "ok"),
        ("Injury-Report_2024-01-31_01PM", datetime(2024, 1, 31, 13, 0), "ok"),
        ("Injury-Report_2024-01-31_01PM", datetime(2024, 1, 31, 13, 59), "ok"),
        ("Injury-Report_2024-01-31_01PM", datetime(2024, 1, 31, 14, 0), "failed"),
        ("Injury-Report_2024-01-31_01PM", datetime(2024, 1, 31, 14, 5), "failed"),
        ("Injury-Report_2024-01-31_01PM", datetime(2024, 1, 31, 12, 59), "failed"),
        ("Injury-Report_2024-01-31_01PM", datetime(2024, 2, 1, 13, 30), "failed"),
        ("Injury-Report_2024-01-31_01PM", None, "unparsed"),
        ("Injury-Report_2026-04-30_01_15PM", datetime(2026, 4, 30, 13, 20), "ok"),
        ("Injury-Report_2026-04-30_01_15PM", datetime(2026, 4, 30, 13, 15), "ok"),
        ("Injury-Report_2026-04-30_01_15PM", datetime(2026, 4, 30, 13, 29), "ok"),
        ("Injury-Report_2026-04-30_01_15PM", datetime(2026, 4, 30, 13, 30), "failed"),
        ("Injury-Report_2026-04-30_01_15PM", datetime(2026, 4, 30, 13, 31), "failed"),
        ("Injury-Report_2026-04-30_01_15PM", datetime(2026, 4, 30, 13, 14), "failed"),
        ("Injury-Report_2026-04-30_01_15PM", datetime(2026, 4, 29, 13, 20), "failed"),
        ("Injury-Report_2026-04-30_01_15PM", None, "unparsed"),
        ("Injury-Report_2021-07-05_10PM", datetime(2021, 7, 5, 22, 37), "ok"),
        ("Injury-Report_2026-01-10_12_00AM", datetime(2026, 1, 10, 0, 0), "ok"),
        ("Injury-Report_2026-01-10_11_45PM", datetime(2026, 1, 10, 23, 59), "ok"),
        ("Injury-Report_2026-01-10_11_45PM", datetime(2026, 1, 11, 0, 0), "failed"),
    ],
)
def test_title_check_uses_the_slot_window(report_id, printed, expected) -> None:
    slot = injury_reports.slot_from_report_id(report_id)
    assert injury_reports.title_check(slot, printed) == expected


def test_printed_to_utc_handles_est_edt_fold_and_gap() -> None:
    assert injury_reports.printed_to_utc(datetime(2024, 1, 31, 17, 30)) == (
        datetime(2024, 1, 31, 22, 30, tzinfo=timezone.utc),
        False,
    )
    assert injury_reports.printed_to_utc(datetime(2024, 7, 1, 17, 30)) == (
        datetime(2024, 7, 1, 21, 30, tzinfo=timezone.utc),
        False,
    )
    # The November switch day: 01:30 names two instants one hour apart.
    assert injury_reports.printed_to_utc(datetime(2024, 11, 3, 1, 30)) == (None, True)
    assert injury_reports.printed_to_utc(datetime(2024, 11, 3, 1, 0)) == (None, True)
    assert injury_reports.printed_to_utc(datetime(2024, 11, 3, 0, 59)) == (
        datetime(2024, 11, 3, 4, 59, tzinfo=timezone.utc),
        False,
    )
    assert injury_reports.printed_to_utc(datetime(2024, 11, 3, 2, 0)) == (
        datetime(2024, 11, 3, 7, 0, tzinfo=timezone.utc),
        False,
    )
    # The March switch day: 02:30 does not exist on the Eastern clock.
    with pytest.raises(injury_reports.InjuryReportError):
        injury_reports.printed_to_utc(datetime(2024, 3, 10, 2, 30))
    with pytest.raises(injury_reports.InjuryReportError):
        injury_reports.printed_to_utc(datetime(2024, 3, 10, 2, 0))
    assert injury_reports.printed_to_utc(datetime(2024, 3, 10, 1, 59)) == (
        datetime(2024, 3, 10, 6, 59, tzinfo=timezone.utc),
        False,
    )
    assert injury_reports.printed_to_utc(datetime(2024, 3, 10, 3, 0)) == (
        datetime(2024, 3, 10, 7, 0, tzinfo=timezone.utc),
        False,
    )
    # Aware input is a caller error, never silently re-localised.
    with pytest.raises(injury_reports.InjuryReportError):
        injury_reports.printed_to_utc(
            datetime(2024, 1, 31, 17, 30, tzinfo=timezone.utc)
        )


# --------------------------------------------------------------------------
# PDF inspection, paths, sidecars and validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(FIXTURE_TITLES))
def test_inspect_pdf_reads_pages_and_title_from_each_fixture(name) -> None:
    payload = (FIXTURES / name).read_bytes()
    inspection = injury_reports.inspect_pdf(payload)

    assert inspection.opens is True
    assert inspection.pages == SOURCES[name]["pages"]
    assert inspection.title_printed_et == FIXTURE_TITLES[name]
    assert inspection.title_text is not None
    assert injury_reports.TITLE_PATTERN.fullmatch(inspection.title_text)
    assert injury_reports.parse_title(inspection.title_text) == FIXTURE_TITLES[name]
    slot = injury_reports.slot_from_report_id(name[: -len(".pdf")])
    assert injury_reports.title_check(slot, inspection.title_printed_et) == "ok"


def test_inspect_pdf_marks_unopenable_bodies() -> None:
    assert injury_reports.inspect_pdf(b"%PDF-1.4 garbage") == injury_reports.PdfInspection(
        opens=False, pages=None, title_text=None, title_printed_et=None,
    )
    assert injury_reports.inspect_pdf(b"") == injury_reports.PdfInspection(
        opens=False, pages=None, title_text=None, title_printed_et=None,
    )
    assert injury_reports.inspect_pdf(b"<html>denied</html>").opens is False


def test_inspect_pdf_without_a_title_opens_but_leaves_the_title_unparsed(
    tmp_path, monkeypatch,
) -> None:
    class FakePage:
        def extract_words(self, **kwargs):
            assert kwargs == {"x_tolerance": 1.5}
            return [{"text": "Hello"}, {"text": "world"}]

    class FakePdf:
        pages = [FakePage(), FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        injury_reports.pdfplumber, "open", lambda stream: FakePdf()
    )
    assert injury_reports.inspect_pdf(b"%PDF-1.4") == injury_reports.PdfInspection(
        opens=True, pages=2, title_text=None, title_printed_et=None,
    )

    class EmptyPdf(FakePdf):
        pages = []

    monkeypatch.setattr(
        injury_reports.pdfplumber, "open", lambda stream: EmptyPdf()
    )
    assert injury_reports.inspect_pdf(b"%PDF-1.4").opens is False


def test_pdf_path_and_metadata(tmp_path) -> None:
    slot = injury_reports.slot_from_report_id("Injury-Report_2024-01-31_05PM")
    assert injury_reports.pdf_path(tmp_path, slot) == (
        tmp_path / "raw/nba_injury_reports/pdfs/2024-01/Injury-Report_2024-01-31_05PM.pdf"
    )
    quarter = injury_reports.slot_from_report_id("Injury-Report_2026-01-10_12_00AM")
    assert injury_reports.pdf_path(tmp_path, quarter) == (
        tmp_path / "raw/nba_injury_reports/pdfs/2026-01/Injury-Report_2026-01-10_12_00AM.pdf"
    )

    payload = (FIXTURES / "Injury-Report_2024-01-31_05PM.pdf").read_bytes()
    inspection = injury_reports.inspect_pdf(payload)
    metadata = injury_reports.pdf_metadata(slot, inspection)
    assert metadata == {
        "provider": "nba_official",
        "resource": "injury_report",
        "url": "https://ak-static.cms.nba.com/referee/injury/Injury-Report_2024-01-31_05PM.pdf",
        "grid": "hour",
        "slot_date": "2024-01-31",
        "slot_label": "05PM",
        "report_id": "Injury-Report_2024-01-31_05PM",
        "opens": True,
        "pages": 7,
        "title_printed_et": "2024-01-31T17:30:00",
        "title_check": "ok",
    }
    assert json.loads(json.dumps(metadata, sort_keys=True)) == metadata

    unopenable = injury_reports.pdf_metadata(
        slot, injury_reports.inspect_pdf(b"%PDF-1.4 garbage")
    )
    assert unopenable["opens"] is False
    assert unopenable["pages"] is None
    assert unopenable["title_printed_et"] is None
    assert unopenable["title_check"] == "unparsed"

    wrong_slot = injury_reports.slot_from_report_id("Injury-Report_2024-01-31_01PM")
    assert injury_reports.pdf_metadata(wrong_slot, inspection)["title_check"] == "failed"


def test_validate_pdf_requires_the_signature(tmp_path) -> None:
    good = tmp_path / "good.pdf"
    good.write_bytes(b"%PDF-1.4 not really a document")
    injury_reports.validate_pdf(good)
    fixture = tmp_path / "fixture.pdf"
    fixture.write_bytes((FIXTURES / "Injury-Report_2019-02-15_01PM.pdf").read_bytes())
    injury_reports.validate_pdf(fixture)

    for body in (b"", b"%PD", b"<html>AccessDenied</html>", b" %PDF-1.4"):
        bad = tmp_path / "bad.pdf"
        bad.write_bytes(body)
        with pytest.raises(injury_reports.InjuryReportError):
            injury_reports.validate_pdf(bad)
    with pytest.raises(injury_reports.InjuryReportError):
        injury_reports.validate_pdf(tmp_path / "missing.pdf")


def test_load_controls_returns_three_entries_with_digests() -> None:
    controls = injury_reports.load_controls()
    assert controls == {
        "Injury-Report_2018-12-17_05PM.pdf": {
            "bytes": 33874,
            "sha256": "1650d479009749f8a0fe2f665a6e808484967b5b37b6b7a9860d892d99b3ed9d",
        },
        "Injury-Report_2024-01-31_05PM.pdf": {
            "bytes": 80020,
            "sha256": "e39fe95bff31019494b82c9ec019919e94d8f10494395bce643d2c1ba02be17c",
        },
        "Injury-Report_2025-12-22_05_30PM.pdf": {
            "bytes": 88537,
            "sha256": "497f46407eaebbfd216b8f9544511675a3e4416856505267de41fc1603d04d30",
        },
    }
    for file_name in controls:
        slot = injury_reports.slot_from_report_id(file_name[: -len(".pdf")])
        assert slot.file_name == file_name
    for name in ("Injury-Report_2018-12-17_05PM.pdf", "Injury-Report_2024-01-31_05PM.pdf"):
        assert controls[name]["sha256"] == SOURCES[name]["sha256"]
        assert controls[name]["bytes"] == SOURCES[name]["bytes"]
    # The result is a copy: mutating it never changes a later load.
    controls.clear()
    assert len(injury_reports.load_controls()) == 3


@pytest.mark.parametrize(
    "document",
    [
        [],
        {"controls": {}},
        {"schema_version": 2, "controls": {}},
        {"schema_version": 1, "controls": []},
        {"schema_version": 1, "controls": {}, "extra": 1},
        {"schema_version": 1, "controls": {"Injury-Report_2024-01-31_05PM.pdf": {"bytes": 1}}},
        {"schema_version": 1, "controls": {"Injury-Report_2024-01-31_05PM.pdf": {"bytes": 1, "sha256": "abc"}}},
        {"schema_version": 1, "controls": {"Injury-Report_2024-01-31_05PM.pdf": {"bytes": 0, "sha256": "0" * 64}}},
        {"schema_version": 1, "controls": {"Injury-Report_2024-01-31_05PM.pdf": {"bytes": True, "sha256": "0" * 64}}},
        {"schema_version": 1, "controls": {"Injury-Report_2024-01-31_05PM.pdf": {"bytes": 1, "sha256": "0" * 64, "note": "x"}}},
        {"schema_version": 1, "controls": {"Injury-Report_2024-01-31_05PM": {"bytes": 1, "sha256": "0" * 64}}},
        {"schema_version": 1, "controls": {"Injury-Report_2024-01-31_17PM.pdf": {"bytes": 1, "sha256": "0" * 64}}},
    ],
)
def test_load_controls_fails_closed_on_malformed_documents(tmp_path, document) -> None:
    path = tmp_path / "controls.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(injury_reports.InjuryReportError):
        injury_reports.load_controls(path)


def test_load_controls_rejects_missing_or_unreadable_files(tmp_path) -> None:
    with pytest.raises(injury_reports.InjuryReportError):
        injury_reports.load_controls(tmp_path / "missing.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(injury_reports.InjuryReportError):
        injury_reports.load_controls(broken)


# --------------------------------------------------------------------------
# inventory generations
# --------------------------------------------------------------------------


def _sidecar(path: Path) -> Path:
    return path.with_name(path.name + ".meta.json")


def _slot_entry(report_id: str, **overrides) -> dict:
    slot = injury_reports.slot_from_report_id(report_id)
    entry = {
        "grid": slot.grid,
        "slot_date": slot.slot_date.isoformat(),
        "slot_label": slot.slot_label,
        "url": slot.url,
        "status": "unavailable",
        "sha256": None,
        "bytes": None,
        "evidence": [],
    }
    entry.update(overrides)
    return entry


def _evidence(fetched_at: str, body: bytes = b"<Error><Code>AccessDenied</Code></Error>") -> dict:
    return {
        "fetched_at": fetched_at,
        "http_status": 403,
        "date_header": "Mon, 09 Nov 2026 14:02:11 GMT",
        "x_amz_request_id": "REQ",
        "x_amz_id_2": "ID2",
        "body_bytes": len(body),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "body_text": body.decode("utf-8"),
    }


def test_inventory_dir_and_generation_number(tmp_path) -> None:
    assert injury_reports.inventory_dir(tmp_path, "2024-01") == (
        tmp_path / "raw/nba_injury_reports/inventory/2024-01"
    )
    assert injury_reports.generation_number(Path("gen-0007.json")) == 7
    assert injury_reports.generation_number(tmp_path / "x/gen-0123.json") == 123
    assert injury_reports.generation_number(Path("gen-10000.json")) == 10000
    for name in ("gen-7.json", "gen-0007.json.meta.json", "gen-0007.txt", "0007.json", "gen-000a.json"):
        with pytest.raises(injury_reports.InjuryReportError):
            injury_reports.generation_number(Path(name))


def test_write_inventory_generation_numbers_above_every_existing_file_and_binds_a_sidecar(
    tmp_path,
) -> None:
    """Would fail if a stray unverifiable generation were overwritten or renumbered."""
    month = "2024-01"
    state = {
        "slots": {"Injury-Report_2024-01-31_05PM": _slot_entry(
            "Injury-Report_2024-01-31_05PM", status="present", sha256="ab" * 32, bytes=10,
        )},
        "controls": [],
    }
    first = injury_reports.write_inventory_generation(tmp_path, month, state)
    second = injury_reports.write_inventory_generation(tmp_path, month, state)
    directory = injury_reports.inventory_dir(tmp_path, month)
    assert first == directory / "gen-0001.json"
    assert second == directory / "gen-0002.json"
    assert injury_reports.generation_number(second) == 2

    body = json.loads(first.read_text(encoding="utf-8"))
    assert body == {
        "schema_version": 1,
        "month": month,
        "generation": 1,
        "slots": state["slots"],
        "controls": [],
    }
    assert first.read_bytes() == (
        json.dumps(body, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    sidecar = json.loads(_sidecar(first).read_text(encoding="utf-8"))
    assert sidecar["provider"] == "nba_official"
    assert sidecar["resource"] == "injury_report_inventory"
    assert sidecar["month"] == month
    assert sidecar["generation"] == 1
    assert sidecar["sha256"] == hashlib.sha256(first.read_bytes()).hexdigest()
    assert sidecar["content_length"] == len(first.read_bytes())
    assert json.loads(_sidecar(second).read_text(encoding="utf-8"))["generation"] == 2
    # The input state is not mutated and the stored copy is independent.
    assert set(state) == {"slots", "controls"}

    # A stray damaged body without a sidecar still claims its number.
    (directory / "gen-0003.json").write_text("{", encoding="utf-8")
    fourth = injury_reports.write_inventory_generation(tmp_path, month, state)
    assert fourth == directory / "gen-0004.json"
    (directory / "gen-0006.json.meta.json").write_text("{}", encoding="utf-8")
    assert injury_reports.write_inventory_generation(tmp_path, month, state) == (
        directory / "gen-0007.json"
    )

    # A state that disagrees with the month or the schema is refused.
    with pytest.raises(injury_reports.InjuryReportError):
        injury_reports.write_inventory_generation(
            tmp_path, month, {**state, "month": "2024-02"}
        )
    with pytest.raises(injury_reports.InjuryReportError):
        injury_reports.write_inventory_generation(
            tmp_path, month, {**state, "schema_version": 2}
        )
    with pytest.raises(injury_reports.InjuryReportError):
        injury_reports.write_inventory_generation(tmp_path, month, {"slots": {}})
    with pytest.raises(injury_reports.InjuryReportError):
        injury_reports.write_inventory_generation(tmp_path, "2018-11", state)


def test_read_current_inventory_returns_highest_verifying_generation_and_lists_damaged_ones(
    tmp_path,
) -> None:
    """Would fail if an interrupted generation (body replaced, sidecar stale) were trusted."""
    month = "2024-01"
    assert injury_reports.read_current_inventory(tmp_path, month) == (None, ())

    one = {"slots": {"Injury-Report_2024-01-01_12AM": _slot_entry("Injury-Report_2024-01-01_12AM")}, "controls": []}
    two = {"slots": {**one["slots"], "Injury-Report_2024-01-01_01AM": _slot_entry("Injury-Report_2024-01-01_01AM")}, "controls": []}
    first = injury_reports.write_inventory_generation(tmp_path, month, one)
    second = injury_reports.write_inventory_generation(tmp_path, month, two)

    current, damaged = injury_reports.read_current_inventory(tmp_path, month)
    assert damaged == ()
    assert current["generation"] == 2
    assert current["month"] == month
    assert set(current["slots"]) == set(two["slots"])

    # Simulated interruption: the body of gen-0002 was replaced but the sidecar was not.
    replaced = json.loads(second.read_text(encoding="utf-8"))
    replaced["slots"]["Injury-Report_2024-01-01_02AM"] = _slot_entry("Injury-Report_2024-01-01_02AM")
    second.write_text(json.dumps(replaced, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    current, damaged = injury_reports.read_current_inventory(tmp_path, month)
    assert damaged == (second,)
    assert current["generation"] == 1
    assert set(current["slots"]) == set(one["slots"])

    # A body without a sidecar and a sidecar without a body are both damaged.
    directory = injury_reports.inventory_dir(tmp_path, month)
    (directory / "gen-0003.json").write_text("{}", encoding="utf-8")
    (directory / "gen-0005.json.meta.json").write_text("{}", encoding="utf-8")
    current, damaged = injury_reports.read_current_inventory(tmp_path, month)
    assert current["generation"] == 1
    assert damaged == (directory / "gen-0005.json", directory / "gen-0003.json", second)
    assert current == json.loads(first.read_text(encoding="utf-8"))


def test_read_current_inventory_rejects_month_mismatch_and_schema_drift(tmp_path) -> None:
    """Would fail if a generation of another month or schema were accepted as current."""
    month = "2024-01"
    directory = injury_reports.inventory_dir(tmp_path, month)
    good = injury_reports.write_inventory_generation(
        tmp_path, month, {"slots": {}, "controls": []}
    )

    def publish(number: int, document: dict) -> Path:
        return cache.publish_bytes(
            directory / f"gen-{number:04d}.json",
            (json.dumps(document, sort_keys=True, indent=2) + "\n").encode("utf-8"),
            {"provider": "nba_official", "resource": "injury_report_inventory",
             "month": month, "generation": number},
            lambda path: None,
        )

    wrong_month = publish(2, {"schema_version": 1, "month": "2024-02", "generation": 2, "slots": {}, "controls": []})
    drifted = publish(3, {"schema_version": 2, "month": month, "generation": 3, "slots": {}, "controls": []})
    renumbered = publish(4, {"schema_version": 1, "month": month, "generation": 9, "slots": {}, "controls": []})
    malformed = publish(5, {"schema_version": 1, "month": month, "generation": 5, "slots": [], "controls": []})

    current, damaged = injury_reports.read_current_inventory(tmp_path, month)
    assert current["generation"] == 1
    assert current == json.loads(good.read_text(encoding="utf-8"))
    assert damaged == (malformed, renumbered, drifted, wrong_month)

    with pytest.raises(injury_reports.InjuryReportError):
        injury_reports.read_current_inventory(tmp_path, "2018-11")


# --------------------------------------------------------------------------
# fetch, controls and recheck
# --------------------------------------------------------------------------


ACCESS_DENIED_BODY = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n<Error><Code>AccessDenied</Code>'
    b'<Message>Access Denied</Message><RequestId>7A9B3C1D2E4F5061</RequestId>'
    b'<HostId>k1Qx+abc/def==</HostId></Error>'
)
ACCESS_DENIED_HEADERS = {
    "Date": "Tue, 10 Nov 2026 14:02:11 GMT",
    "x-amz-request-id": "7A9B3C1D2E4F5061",
    "x-amz-id-2": "k1Qx+abc/def==",
    "Content-Type": "application/xml",
}
CONTROL_FIXTURES = (
    "Injury-Report_2018-12-17_05PM.pdf",
    "Injury-Report_2019-02-15_01PM.pdf",
    "Injury-Report_2026-01-10_12_00AM.pdf",
)
PRESENT_FIXTURE = "Injury-Report_2024-01-31_05PM.pdf"
FIXED_NOW = datetime(2026, 11, 10, 14, 2, 11, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, status_code, content, headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = dict(headers or {})


class FakeSession:
    """Serves responses by URL: a list is consumed in order, a single response is sticky."""

    def __init__(self, routes=None, default=None):
        self.routes = dict(routes or {})
        self.default = default
        self.request_log = []

    @property
    def calls(self) -> int:
        return len(self.request_log)

    def urls(self) -> list[str]:
        return [entry["url"] for entry in self.request_log]

    def get(self, url, **kwargs):
        self.request_log.append({"url": url, **kwargs})
        if url in self.routes:
            item = self.routes[url]
            if isinstance(item, list):
                if not item:
                    raise AssertionError(f"FakeSession exhausted responses for {url}")
                item = item.pop(0)
        elif self.default is not None:
            item = self.default
        else:
            raise AssertionError(f"FakeSession has no response for {url}")
        if isinstance(item, BaseException):
            raise item
        return item


class FakeClock:
    def __init__(self):
        self.value = 0.0
        self.sleeps = []

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds


class TickingUtc:
    """Returns FIXED_NOW advanced by one second per call."""

    def __init__(self, start: datetime = FIXED_NOW):
        self.current = start

    def __call__(self) -> datetime:
        value = self.current
        self.current = value.replace(microsecond=0) + timedelta(seconds=1)
        return value


def _no_wait_pacer():
    return injury_reports.RequestPacer(now=lambda: 0.0, sleeper=lambda seconds: None)


def _fail_sleeper(seconds):
    raise AssertionError(f"unexpected retry sleep of {seconds} s")


def _pdf(name: str = PRESENT_FIXTURE) -> FakeResponse:
    return FakeResponse(200, (FIXTURES / name).read_bytes(), {"Content-Type": "application/pdf"})


def _denied() -> FakeResponse:
    return FakeResponse(403, ACCESS_DENIED_BODY, ACCESS_DENIED_HEADERS)


def _control_url(name: str) -> str:
    return injury_reports.PDF_BASE_URL + name


def _fetch_environment(monkeypatch, report_ids, *, now=None):
    """Trim the worklist to ``report_ids``, fake the controls and freeze the UTC clock."""
    slots = tuple(injury_reports.slot_from_report_id(report_id) for report_id in report_ids)

    def fake_enumerate(months=None):
        if months is None:
            return slots
        selected = set(months)
        return tuple(slot for slot in slots if slot.month in selected)

    monkeypatch.setattr(injury_reports, "enumerate_slots", fake_enumerate)
    controls = {}
    for name in CONTROL_FIXTURES:
        payload = (FIXTURES / name).read_bytes()
        controls[name] = {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    monkeypatch.setattr(injury_reports, "load_controls", lambda path=None: controls)
    monkeypatch.setattr(injury_reports, "_utc_now", now if now is not None else TickingUtc())
    routes = {_control_url(name): _pdf(name) for name in CONTROL_FIXTURES}
    return slots, routes


def _run(tmp_path, session, **kwargs):
    kwargs.setdefault("pacer", _no_wait_pacer())
    kwargs.setdefault("sleeper", _fail_sleeper)
    kwargs.setdefault("today", FIXED_NOW.date())
    return injury_reports.fetch(
        tmp_path, kwargs.pop("months", ["2024-01"]), session=session, **kwargs,
    )


def _generations(tmp_path, month="2024-01") -> list[Path]:
    directory = injury_reports.inventory_dir(tmp_path, month)
    if not directory.exists():
        return []
    return sorted(path for path in directory.iterdir() if path.name.endswith(".json") and ".meta" not in path.name)


def _publish_pdf(tmp_path, report_id: str, name: str = PRESENT_FIXTURE) -> Path:
    slot = injury_reports.slot_from_report_id(report_id)
    payload = (FIXTURES / name).read_bytes()
    return cache.publish_bytes(
        injury_reports.pdf_path(tmp_path, slot),
        payload,
        injury_reports.pdf_metadata(slot, injury_reports.inspect_pdf(payload)),
        injury_reports.validate_pdf,
    )


def test_check_control_requests_the_file_and_fails_closed_on_any_other_body() -> None:
    controls = {}
    for name in CONTROL_FIXTURES:
        payload = (FIXTURES / name).read_bytes()
        controls[name] = {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    name = CONTROL_FIXTURES[0]
    session = FakeSession({_control_url(name): _pdf(name)})
    clock = FakeClock()
    pacer = injury_reports.RequestPacer(now=clock.now, sleeper=clock.sleep)

    result = injury_reports.check_control(session, name, controls[name], pacer=pacer)

    assert result == {
        "checked_at": result["checked_at"],
        "file_name": name,
        "sha256": controls[name]["sha256"],
        "ok": True,
    }
    datetime.strptime(result["checked_at"], "%Y-%m-%dT%H:%M:%SZ")
    assert session.request_log == [{
        "url": _control_url(name),
        "headers": {"User-Agent": injury_reports.USER_AGENT},
        "timeout": 60,
        "allow_redirects": False,
    }]
    assert pacer.last_started is not None

    for bad in (
        _pdf(CONTROL_FIXTURES[1]),
        _denied(),
        FakeResponse(503, b"", {}),
        FakeResponse(200, b"<html>blocked</html>", {}),
        FakeResponse(200, (FIXTURES / name).read_bytes() + b"\n", {}),
        requests.ConnectionError("reset"),
    ):
        with pytest.raises(cache.SourceAccessError):
            injury_reports.check_control(
                FakeSession({_control_url(name): bad}), name, controls[name], pacer=_no_wait_pacer(),
            )


def test_fetch_runs_a_control_before_the_first_probe_and_fails_closed_on_a_bad_control(
    tmp_path, monkeypatch,
) -> None:
    """Would fail if a probe or a generation preceded the start control."""
    slots, routes = _fetch_environment(
        monkeypatch, ["Injury-Report_2024-01-31_05PM", "Injury-Report_2024-01-31_06PM"],
    )
    bad_name = CONTROL_FIXTURES[1]
    routes[_control_url(bad_name)] = FakeResponse(200, b"%PDF-1.4 not the control body", {})
    session = FakeSession(routes, default=_pdf())

    with pytest.raises(cache.SourceAccessError):
        _run(tmp_path, session)

    assert session.urls() == [_control_url(CONTROL_FIXTURES[0]), _control_url(bad_name)]
    assert not (tmp_path / "raw").exists()
    assert _generations(tmp_path) == []

    # A control that is answered with the host's 403 is a failure too, never "unavailable".
    routes[_control_url(bad_name)] = _denied()
    session = FakeSession(routes, default=_pdf())
    with pytest.raises(cache.SourceAccessError):
        _run(tmp_path, session)
    assert session.calls == 2
    assert _generations(tmp_path) == []


def test_fetch_archives_pdfs_and_records_unavailable_evidence_verbatim(
    tmp_path, monkeypatch,
) -> None:
    """Would fail if the PDF sidecar or the 403 evidence fields drifted from the contract."""
    present_id = "Injury-Report_2024-01-31_05PM"
    missing_id = "Injury-Report_2024-01-31_03AM"
    slots, routes = _fetch_environment(monkeypatch, [missing_id, present_id])
    present, missing = slots[1], slots[0]
    routes[present.url] = _pdf()
    routes[missing.url] = _denied()
    session = FakeSession(routes)

    summary = _run(tmp_path, session)

    assert summary == {
        "probed": 2,
        "present": 1,
        "unavailable": 1,
        "rechecked": 0,
        "skipped_today": 0,
        "generations_written": 2,
    }
    for entry in session.request_log:
        assert entry["headers"] == {"User-Agent": injury_reports.USER_AGENT}
        assert entry["timeout"] == injury_reports.TIMEOUT_S == 60
        assert entry["allow_redirects"] is False
    control_urls = [_control_url(name) for name in CONTROL_FIXTURES]
    assert session.urls() == control_urls + [missing.url, present.url] + control_urls

    payload = (FIXTURES / PRESENT_FIXTURE).read_bytes()
    body_path = injury_reports.pdf_path(tmp_path, present)
    assert body_path.read_bytes() == payload
    sidecar = json.loads(_sidecar(body_path).read_text(encoding="utf-8"))
    expected = injury_reports.pdf_metadata(present, injury_reports.inspect_pdf(payload))
    assert expected["title_check"] == "ok"
    assert {key: sidecar[key] for key in expected} == expected
    assert sidecar["sha256"] == hashlib.sha256(payload).hexdigest()
    assert sidecar["content_length"] == len(payload)
    assert not injury_reports.pdf_path(tmp_path, missing).exists()

    state, damaged = injury_reports.read_current_inventory(tmp_path, "2024-01")
    assert damaged == ()
    assert state["generation"] == 2
    assert set(state["slots"]) == {present_id, missing_id}
    assert state["slots"][present_id] == {
        "grid": "hour",
        "slot_date": "2024-01-31",
        "slot_label": "05PM",
        "url": present.url,
        "status": "present",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "evidence": [],
    }
    assert state["slots"][missing_id] == {
        "grid": "hour",
        "slot_date": "2024-01-31",
        "slot_label": "03AM",
        "url": missing.url,
        "status": "unavailable",
        "sha256": None,
        "bytes": None,
        "evidence": [{
            "fetched_at": "2026-11-10T14:02:14Z",
            "http_status": 403,
            "date_header": "Tue, 10 Nov 2026 14:02:11 GMT",
            "x_amz_request_id": "7A9B3C1D2E4F5061",
            "x_amz_id_2": "k1Qx+abc/def==",
            "body_bytes": len(ACCESS_DENIED_BODY),
            "body_sha256": hashlib.sha256(ACCESS_DENIED_BODY).hexdigest(),
            "body_text": ACCESS_DENIED_BODY.decode("utf-8"),
        }],
    }
    assert [control["file_name"] for control in state["controls"]] == list(CONTROL_FIXTURES) * 2
    assert all(control["ok"] is True for control in state["controls"])
    assert state["controls"][0]["checked_at"] == "2026-11-10T14:02:11Z"
    generation_sidecar = json.loads(
        _sidecar(injury_reports.inventory_dir(tmp_path, "2024-01") / "gen-0002.json").read_text(encoding="utf-8")
    )
    assert {generation_sidecar[key] for key in ("provider", "resource")} == {"nba_official", "injury_report_inventory"}
    assert generation_sidecar["month"] == "2024-01"
    assert generation_sidecar["generation"] == 2

    # Missing S3 headers are recorded as null, never invented.
    other_id = "Injury-Report_2024-01-31_04AM"
    slots, routes = _fetch_environment(monkeypatch, [other_id])
    routes[slots[0].url] = FakeResponse(403, ACCESS_DENIED_BODY, {})
    _run(tmp_path, FakeSession(routes))
    state, _ = injury_reports.read_current_inventory(tmp_path, "2024-01")
    evidence = state["slots"][other_id]["evidence"][0]
    assert evidence["date_header"] is None
    assert evidence["x_amz_request_id"] is None
    assert evidence["x_amz_id_2"] is None
    assert evidence["body_text"] == ACCESS_DENIED_BODY.decode("utf-8")


def test_fetch_writes_one_generation_per_touched_month_only_after_a_successful_control(
    tmp_path, monkeypatch,
) -> None:
    """Would fail if controls or generations drifted from start / every 200 / closing."""
    all_slots = injury_reports.enumerate_slots(months=["2024-01"])[:450]
    january = all_slots[:2]
    february = injury_reports.enumerate_slots(months=["2024-02"])[:3]
    slots, routes = _fetch_environment(monkeypatch, [slot.report_id for slot in all_slots])
    session = FakeSession(routes, default=_denied())

    summary = _run(tmp_path, session)

    assert summary["probed"] == 450
    assert summary["unavailable"] == 450
    assert summary["present"] == 0
    assert summary["generations_written"] == 4
    control_urls = [_control_url(name) for name in CONTROL_FIXTURES]
    slot_urls = [slot.url for slot in slots]
    assert session.urls() == (
        control_urls + slot_urls[:200] + control_urls + slot_urls[200:400]
        + control_urls + slot_urls[400:] + control_urls
    )
    generations = _generations(tmp_path)
    assert [path.name for path in generations] == [
        "gen-0001.json", "gen-0002.json", "gen-0003.json", "gen-0004.json",
    ]
    counts = []
    for path in generations:
        document = json.loads(path.read_text(encoding="utf-8"))
        counts.append((len(document["slots"]), len(document["controls"])))
        assert set(document["slots"]) == {slot.report_id for slot in slots[: len(document["slots"])]}
        assert list(document["slots"]) == sorted(document["slots"])
    assert counts == [(0, 3), (200, 6), (400, 9), (450, 12)]
    state, damaged = injury_reports.read_current_inventory(tmp_path, "2024-01")
    assert damaged == ()
    assert state["generation"] == 4

    # Two touched months each get their own generations; an untouched month gets none.
    slots, routes = _fetch_environment(
        monkeypatch, [slot.report_id for slot in (*january, *february)],
    )
    other = tmp_path / "other"
    summary = _run(other, FakeSession(routes, default=_denied()), months=["2024-01", "2024-02", "2024-03"])
    assert summary["probed"] == 5
    assert summary["generations_written"] == 4
    assert [path.name for path in _generations(other, "2024-01")] == ["gen-0001.json", "gen-0002.json"]
    assert [path.name for path in _generations(other, "2024-02")] == ["gen-0001.json", "gen-0002.json"]
    assert _generations(other, "2024-03") == []
    assert len(json.loads(_generations(other, "2024-02")[-1].read_text())["slots"]) == 3


def test_fetch_discards_the_unpersisted_batch_when_a_mid_run_control_fails(
    tmp_path, monkeypatch,
) -> None:
    """Would fail if outcomes gathered before a failed control were persisted."""
    all_slots = injury_reports.enumerate_slots(months=["2024-01"])[:250]
    slots, routes = _fetch_environment(monkeypatch, [slot.report_id for slot in all_slots])
    present = slots[230]
    routes[present.url] = _pdf()
    bad_name = CONTROL_FIXTURES[2]
    routes[_control_url(bad_name)] = [
        _pdf(bad_name), _pdf(bad_name), FakeResponse(503, b"", {}),
    ]
    session = FakeSession(routes, default=_denied())

    with pytest.raises(cache.SourceAccessError):
        _run(tmp_path, session)

    control_urls = [_control_url(name) for name in CONTROL_FIXTURES]
    slot_urls = [slot.url for slot in slots]
    assert session.urls() == (
        control_urls + slot_urls[:200] + control_urls + slot_urls[200:] + control_urls
    )
    generations = _generations(tmp_path)
    assert [path.name for path in generations] == ["gen-0001.json", "gen-0002.json"]
    state, damaged = injury_reports.read_current_inventory(tmp_path, "2024-01")
    assert damaged == ()
    assert state["generation"] == 2
    assert set(state["slots"]) == {slot.report_id for slot in slots[:200]}
    assert present.report_id not in state["slots"]
    # The already-published PDF pair of the discarded batch stays on disk.
    body_path = injury_reports.pdf_path(tmp_path, present)
    assert body_path.read_bytes() == (FIXTURES / PRESENT_FIXTURE).read_bytes()
    assert _sidecar(body_path).exists()
    pdf_root = tmp_path / "raw/nba_injury_reports/pdfs"
    assert sorted(path.name for path in pdf_root.rglob("*.pdf")) == [present.file_name]


def test_fetch_resumes_from_pdfs_on_disk_and_the_previous_generation(
    tmp_path, monkeypatch,
) -> None:
    """Would fail if a published PDF without a generation entry were re-requested or lost."""
    ids = [
        "Injury-Report_2024-01-31_01PM",   # unavailable in gen-0001
        "Injury-Report_2024-01-31_02PM",   # present in gen-0001, PDF on disk
        "Injury-Report_2024-01-31_03PM",   # PDF on disk, no generation entry (interrupted run)
        "Injury-Report_2024-01-31_04PM",   # never probed
    ]
    slots, routes = _fetch_environment(monkeypatch, ids)
    unavailable, recorded, orphan, fresh = slots
    recorded_body = _publish_pdf(tmp_path, recorded.report_id, "Injury-Report_2019-02-15_01PM.pdf")
    orphan_body = _publish_pdf(tmp_path, orphan.report_id)
    recorded_payload = recorded_body.read_bytes()
    history = _evidence("2026-11-09T10:00:00Z")
    injury_reports.write_inventory_generation(tmp_path, "2024-01", {
        "slots": {
            unavailable.report_id: _slot_entry(unavailable.report_id, evidence=[history]),
            recorded.report_id: _slot_entry(
                recorded.report_id, status="present",
                sha256=hashlib.sha256(recorded_payload).hexdigest(), bytes=len(recorded_payload),
            ),
        },
        "controls": [{"checked_at": "2026-11-09T09:59:00Z", "file_name": CONTROL_FIXTURES[0],
                      "sha256": "0" * 64, "ok": True}],
    })
    routes[fresh.url] = _denied()
    session = FakeSession(routes)

    summary = _run(tmp_path, session)

    control_urls = [_control_url(name) for name in CONTROL_FIXTURES]
    assert session.urls() == control_urls + [fresh.url] + control_urls
    assert summary == {
        "probed": 1, "present": 0, "unavailable": 1, "rechecked": 0,
        "skipped_today": 0, "generations_written": 2,
    }
    state, damaged = injury_reports.read_current_inventory(tmp_path, "2024-01")
    assert damaged == ()
    assert state["generation"] == 3
    assert set(state["slots"]) == set(ids)
    assert state["slots"][unavailable.report_id] == _slot_entry(unavailable.report_id, evidence=[history])
    assert state["slots"][recorded.report_id]["status"] == "present"
    assert state["slots"][recorded.report_id]["sha256"] == hashlib.sha256(recorded_payload).hexdigest()
    orphan_payload = orphan_body.read_bytes()
    assert state["slots"][orphan.report_id] == {
        **_slot_entry(orphan.report_id, status="present"),
        "sha256": hashlib.sha256(orphan_payload).hexdigest(),
        "bytes": len(orphan_payload),
    }
    assert state["slots"][fresh.report_id]["status"] == "unavailable"
    assert len(state["slots"][fresh.report_id]["evidence"]) == 1
    assert state["controls"][0]["checked_at"] == "2026-11-09T09:59:00Z"
    assert len(state["controls"]) == 7
    assert recorded_body.read_bytes() == recorded_payload
    assert orphan_body.read_bytes() == orphan_payload

    # The merged state is persisted at the start control even when nothing is probed.
    second = tmp_path / "second"
    slots, routes = _fetch_environment(monkeypatch, ids[2:3])
    _publish_pdf(second, orphan.report_id)
    session = FakeSession(routes)
    summary = _run(second, session)
    assert session.urls() == control_urls
    assert summary["probed"] == 0 and summary["generations_written"] == 1
    state, _ = injury_reports.read_current_inventory(second, "2024-01")
    assert state["slots"][orphan.report_id]["status"] == "present"

    # A generation that claims a present slot whose PDF is gone fails closed.
    third = tmp_path / "third"
    slots, routes = _fetch_environment(monkeypatch, ids[1:2])
    injury_reports.write_inventory_generation(third, "2024-01", {
        "slots": {recorded.report_id: _slot_entry(
            recorded.report_id, status="present", sha256="0" * 64, bytes=1,
        )},
        "controls": [],
    })
    with pytest.raises(injury_reports.InjuryReportError):
        _run(third, FakeSession(routes))

    # A verifying generation and a verifying pair that disagree on the body fail closed.
    fifth = tmp_path / "fifth"
    slots, routes = _fetch_environment(monkeypatch, ids[1:2])
    _publish_pdf(fifth, recorded.report_id, "Injury-Report_2019-02-15_01PM.pdf")
    injury_reports.write_inventory_generation(fifth, "2024-01", {
        "slots": {recorded.report_id: _slot_entry(
            recorded.report_id, status="present",
            sha256=hashlib.sha256(orphan_payload).hexdigest(), bytes=len(orphan_payload),
        )},
        "controls": [],
    })
    session = FakeSession(routes, default=_pdf())
    with pytest.raises(injury_reports.InjuryReportError) as exc_info:
        _run(fifth, session)
    assert recorded.report_id in str(exc_info.value)
    assert hashlib.sha256(orphan_payload).hexdigest() in str(exc_info.value)
    assert hashlib.sha256(recorded_payload).hexdigest() in str(exc_info.value)
    assert session.calls == 0
    assert len(_generations(fifth)) == 1

    # A damaged PDF pair is a hard failure, never re-requested.
    fourth = tmp_path / "fourth"
    slots, routes = _fetch_environment(monkeypatch, ids[2:3])
    body = _publish_pdf(fourth, orphan.report_id)
    body.write_bytes(b"%PDF-1.4 tampered")
    with pytest.raises(cache.CacheIntegrityError):
        _run(fourth, FakeSession(routes, default=_pdf()))


def test_fetch_retries_transient_errors_and_stops_on_other_outcomes(
    tmp_path, monkeypatch,
) -> None:
    """Would fail if 503s were not retried or a non-PDF 200 / odd 403 slipped through."""
    report_id = "Injury-Report_2024-01-31_05PM"
    slots, routes = _fetch_environment(monkeypatch, [report_id])
    slot = slots[0]
    routes[slot.url] = [
        FakeResponse(503, b"", {}), requests.ConnectionError("reset"), _pdf(),
    ]
    sleeps = []
    session = FakeSession(routes)

    summary = _run(tmp_path, session, sleeper=sleeps.append)

    assert sleeps == [5.0, 10.0]
    assert session.urls().count(slot.url) == 3
    assert summary["present"] == 1 and summary["probed"] == 1
    assert injury_reports.pdf_path(tmp_path, slot).exists()

    # A 200 that is not a PDF is retried five times, then the run stops.
    other = tmp_path / "html"
    slots, routes = _fetch_environment(monkeypatch, [report_id])
    routes[slot.url] = FakeResponse(200, b"<html>maintenance</html>", {"Content-Type": "text/html"})
    sleeps = []
    session = FakeSession(routes)
    with pytest.raises(cache.SourceAccessError):
        _run(other, session, sleeper=sleeps.append)
    assert sleeps == [5.0, 10.0, 20.0, 40.0]
    assert session.urls().count(slot.url) == 5
    assert not injury_reports.pdf_path(other, slot).exists()
    assert _generations(other) == [_generations(other)[0]]   # only the start generation
    assert json.loads(_generations(other)[0].read_text())["slots"] == {}

    # A 403 with a body over 1,024 bytes is not "unavailable": retry path, then stop.
    for body in (
        b"<Error><Code>AccessDenied</Code></Error>" + b" " * 2048,
        b"<html><body>Forbidden</body></html>",
        b"<Error><Code>NoSuchKey</Code></Error>",
    ):
        another = tmp_path / hashlib.sha256(body).hexdigest()[:8]
        slots, routes = _fetch_environment(monkeypatch, [report_id])
        routes[slot.url] = FakeResponse(403, body, ACCESS_DENIED_HEADERS)
        sleeps = []
        session = FakeSession(routes)
        with pytest.raises(cache.SourceAccessError):
            _run(another, session, sleeper=sleeps.append)
        assert sleeps == [5.0, 10.0, 20.0, 40.0]
        assert session.urls().count(slot.url) == 5
        assert json.loads(_generations(another)[-1].read_text())["slots"] == {}

    # Exactly 1,024 bytes still qualifies.
    edge_body = ACCESS_DENIED_BODY + b" " * (1024 - len(ACCESS_DENIED_BODY))
    assert len(edge_body) == injury_reports.MAX_ACCESS_DENIED_BYTES
    edge = tmp_path / "edge"
    slots, routes = _fetch_environment(monkeypatch, [report_id])
    routes[slot.url] = FakeResponse(403, edge_body, ACCESS_DENIED_HEADERS)
    summary = _run(edge, FakeSession(routes))
    assert summary["unavailable"] == 1
    state, _ = injury_reports.read_current_inventory(edge, "2024-01")
    assert state["slots"][report_id]["evidence"][0]["body_bytes"] == 1024

    # Other statuses (404, 301) take the retry path too and never create a record.
    for response in (FakeResponse(404, b"", {}), FakeResponse(301, b"", {"Location": "https://x/"})):
        yet = tmp_path / f"status-{response.status_code}"
        slots, routes = _fetch_environment(monkeypatch, [report_id])
        routes[slot.url] = response
        sleeps = []
        with pytest.raises(cache.SourceAccessError):
            _run(yet, FakeSession(routes), sleeper=sleeps.append)
        assert sleeps == [5.0, 10.0, 20.0, 40.0]


def test_fetch_never_rerequests_a_present_slot_and_never_replaces_a_body(
    tmp_path, monkeypatch,
) -> None:
    """Would fail if a present slot were requested again or its body rewritten."""
    present_id = "Injury-Report_2024-01-31_05PM"
    fresh_id = "Injury-Report_2024-01-31_06PM"
    slots, routes = _fetch_environment(monkeypatch, [present_id, fresh_id])
    present, fresh = slots
    body_path = _publish_pdf(tmp_path, present_id)
    before = body_path.read_bytes()
    sidecar_before = _sidecar(body_path).read_bytes()
    stat_before = body_path.stat()
    injury_reports.write_inventory_generation(tmp_path, "2024-01", {
        "slots": {present_id: _slot_entry(
            present_id, status="present",
            sha256=hashlib.sha256(before).hexdigest(), bytes=len(before),
        )},
        "controls": [],
    })
    routes[fresh.url] = _pdf("Injury-Report_2021-04-01_05PM.pdf")
    session = FakeSession(routes)

    for recheck in (False, True):
        summary = _run(tmp_path, session, recheck_unavailable=recheck)
        assert present.url not in session.urls()
        assert summary["present"] == (1 if not recheck else 0)
    assert session.urls().count(fresh.url) == 1
    assert body_path.read_bytes() == before
    assert _sidecar(body_path).read_bytes() == sidecar_before
    assert body_path.stat().st_mtime_ns == stat_before.st_mtime_ns
    state, _ = injury_reports.read_current_inventory(tmp_path, "2024-01")
    assert state["slots"][present_id]["sha256"] == hashlib.sha256(before).hexdigest()
    assert state["slots"][fresh_id]["status"] == "present"

    # Nothing to do at all: no request is sent, no generation is written.
    slots, routes = _fetch_environment(monkeypatch, [present_id, fresh_id])
    session = FakeSession(routes)
    count = len(_generations(tmp_path))
    summary = _run(tmp_path, session, recheck_unavailable=True)
    assert session.calls == 0
    assert summary == {
        "probed": 0, "present": 0, "unavailable": 0, "rechecked": 0,
        "skipped_today": 0, "generations_written": 0,
    }
    assert len(_generations(tmp_path)) == count


def test_recheck_unavailable_rerequests_only_unavailable_slots_dated_before_today(
    tmp_path, monkeypatch,
) -> None:
    """Would fail if a same-day recheck ran, history were dropped or a present slot re-fetched."""
    ids = [
        "Injury-Report_2024-01-31_01PM",   # unavailable yesterday -> still 403
        "Injury-Report_2024-01-31_02PM",   # unavailable yesterday -> now a PDF
        "Injury-Report_2024-01-31_03PM",   # unavailable today -> skipped
        "Injury-Report_2024-01-31_04PM",   # present -> untouched
        "Injury-Report_2024-01-31_05PM",   # never probed -> probed as usual
    ]
    slots, routes = _fetch_environment(monkeypatch, ids)
    still, recovered, today_slot, present, fresh = slots
    body_path = _publish_pdf(tmp_path, present.report_id, "Injury-Report_2019-09-30_01PM.pdf")
    present_payload = body_path.read_bytes()
    old_evidence = {
        still.report_id: _evidence("2026-11-09T23:59:59Z", b"<Error><Code>AccessDenied</Code>1</Error>"),
        recovered.report_id: _evidence("2026-11-08T12:00:00Z", b"<Error><Code>AccessDenied</Code>2</Error>"),
        today_slot.report_id: _evidence("2026-11-10T00:00:00Z", b"<Error><Code>AccessDenied</Code>3</Error>"),
    }
    injury_reports.write_inventory_generation(tmp_path, "2024-01", {
        "slots": {
            still.report_id: _slot_entry(still.report_id, evidence=[old_evidence[still.report_id]]),
            recovered.report_id: _slot_entry(recovered.report_id, evidence=[old_evidence[recovered.report_id]]),
            today_slot.report_id: _slot_entry(today_slot.report_id, evidence=[old_evidence[today_slot.report_id]]),
            present.report_id: _slot_entry(
                present.report_id, status="present",
                sha256=hashlib.sha256(present_payload).hexdigest(), bytes=len(present_payload),
            ),
        },
        "controls": [],
    })
    routes[still.url] = _denied()
    routes[recovered.url] = _pdf("Injury-Report_2021-04-01_05PM.pdf")
    routes[fresh.url] = _denied()
    session = FakeSession(routes)

    summary = _run(tmp_path, session, recheck_unavailable=True)

    control_urls = [_control_url(name) for name in CONTROL_FIXTURES]
    assert session.urls() == control_urls + [fresh.url, still.url, recovered.url] + control_urls
    assert summary == {
        "probed": 3, "present": 1, "unavailable": 2, "rechecked": 2,
        "skipped_today": 1, "generations_written": 2,
    }
    state, damaged = injury_reports.read_current_inventory(tmp_path, "2024-01")
    assert damaged == ()
    assert set(state["slots"]) == set(ids)

    still_entry = state["slots"][still.report_id]
    assert still_entry["status"] == "unavailable"
    assert still_entry["sha256"] is None and still_entry["bytes"] is None
    assert len(still_entry["evidence"]) == 2
    assert still_entry["evidence"][0] == old_evidence[still.report_id]
    assert still_entry["evidence"][1]["body_text"] == ACCESS_DENIED_BODY.decode("utf-8")
    assert still_entry["evidence"][1]["fetched_at"].startswith("2026-11-10T")

    recovered_payload = (FIXTURES / "Injury-Report_2021-04-01_05PM.pdf").read_bytes()
    recovered_entry = state["slots"][recovered.report_id]
    assert recovered_entry["status"] == "present"
    assert recovered_entry["sha256"] == hashlib.sha256(recovered_payload).hexdigest()
    assert recovered_entry["bytes"] == len(recovered_payload)
    assert recovered_entry["evidence"] == [old_evidence[recovered.report_id]]
    recovered_path = injury_reports.pdf_path(tmp_path, recovered)
    assert recovered_path.read_bytes() == recovered_payload
    sidecar = json.loads(_sidecar(recovered_path).read_text(encoding="utf-8"))
    assert sidecar["report_id"] == recovered.report_id
    assert sidecar["title_check"] == "failed"

    assert state["slots"][today_slot.report_id] == _slot_entry(
        today_slot.report_id, evidence=[old_evidence[today_slot.report_id]],
    )
    assert state["slots"][present.report_id]["status"] == "present"
    assert body_path.read_bytes() == present_payload
    assert state["slots"][fresh.report_id]["status"] == "unavailable"

    # Without the flag the same tree re-requests nothing.
    session = FakeSession(routes)
    summary = _run(tmp_path, session)
    assert session.calls == 0
    assert summary["rechecked"] == 0 and summary["skipped_today"] == 0

    # A later calendar day rechecks the two still-unavailable slots.
    routes[still.url] = _denied()
    routes[today_slot.url] = _denied()
    routes[fresh.url] = _denied()
    session = FakeSession(routes)
    summary = _run(
        tmp_path, session, recheck_unavailable=True, today=FIXED_NOW.date() + timedelta(days=1),
    )
    assert sorted(url for url in session.urls() if url not in control_urls) == sorted(
        [still.url, today_slot.url, fresh.url]
    )
    assert summary["rechecked"] == 3 and summary["probed"] == 3 and summary["skipped_today"] == 0


def test_fetch_paces_probes_at_least_half_a_second_apart(tmp_path, monkeypatch) -> None:
    """Would fail if consecutive request starts were closer than 0.5 s."""
    slots, routes = _fetch_environment(
        monkeypatch, ["Injury-Report_2024-01-31_05PM", "Injury-Report_2024-01-31_06PM"],
    )
    clock = FakeClock()
    pacer = injury_reports.RequestPacer(now=clock.now, sleeper=clock.sleep)
    session = FakeSession(routes, default=_denied())

    _run(tmp_path, session, pacer=pacer)

    assert session.calls == 8
    assert clock.sleeps == [0.5] * 7
    assert pacer.minimum_interval_s == injury_reports.MIN_REQUEST_INTERVAL_S == 0.5
    with pytest.raises(ValueError):
        injury_reports.RequestPacer(minimum_interval_s=0.25)

    # The default pacer is used when none is passed.
    created = []
    original = injury_reports.RequestPacer

    class Recording(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(injury_reports, "RequestPacer", Recording)
    slots, routes = _fetch_environment(monkeypatch, ["Injury-Report_2024-01-31_07PM"])
    injury_reports.fetch(
        tmp_path / "default", ["2024-01"], session=FakeSession(routes, default=_denied()),
        sleeper=_fail_sleeper, today=FIXED_NOW.date(),
    )
    assert len(created) == 1 and created[0].minimum_interval_s == 0.5
