"""Tests for official injury-report slot grids, titles and PDF inspection."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from nba_data import injury_reports


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
