"""Throwaway spike (2026-11-03): build the Excel working copy of the development-history pilot.
The CSVs in this folder stay the source of truth; the workbook is for hand entry (dropdown validation, no formulas).
Usage: python build_pilot_workbook.py <pilot dir> <output.xlsx>"""
import sys, pathlib, pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
P, OUT = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
F, FB = Font(name="Arial", size=10), Font(name="Arial", size=10, bold=True)
HEAD, INPUT = PatternFill("solid", fgColor="D9D9D9"), PatternFill("solid", fgColor="FFFF00")
WRAP = Alignment(wrap_text=True, vertical="top")
sample = pd.read_csv(P / "pilot_sample.csv"); recs = pd.read_csv(P / "participation_records.csv", dtype=str).fillna("")
log = pd.read_csv(P / "research_log.csv", dtype=str).fillna(""); strata = pd.read_csv(P / "pool_strata_summary.csv")
LISTS = {
    "record_type": ["high_school", "prep_postgrad", "reclassification", "grassroots_circuit", "other_sport", "college", "pre_nba_pro", "g_league", "national_team", "offseason_competition", "prior_injury", "other"],
    "claim_kind": ["participation", "explicit_absence"],
    "source_type": ["media_guide", "league_record", "federation", "school", "news", "encyclopedia", "recruiting", "forum"],
    "confidence": ["confirmed", "reported", "weak"],
    "player_name": sorted(sample.player_name.tolist()),
    "true_false": ["TRUE", "FALSE"],
}
def write_table(ws, df, input_cols=(), text_cols=(), widths=None, blank_rows=0):
    for j, col in enumerate(df.columns, 1):
        c = ws.cell(row=1, column=j, value=col); c.font = FB; c.fill = INPUT if col in input_cols else HEAD; c.alignment = WRAP
    for i, row in enumerate(df.itertuples(index=False), 2):
        for j, v in enumerate(row, 1):
            c = ws.cell(row=i, column=j, value=(None if (isinstance(v, float) and pd.isna(v)) else v)); c.font = F; c.alignment = WRAP
            if df.columns[j - 1] in text_cols: c.number_format = "@"
    for i in range(len(df) + 2, len(df) + 2 + blank_rows):
        for j, col in enumerate(df.columns, 1):
            c = ws.cell(row=i, column=j); c.font = F; c.alignment = WRAP
            if col in text_cols: c.number_format = "@"
    for j, col in enumerate(df.columns, 1): ws.column_dimensions[get_column_letter(j)].width = (widths or {}).get(col, 14)
    ws.freeze_panes = "A2"; ws.auto_filter.ref = f"A1:{get_column_letter(len(df.columns))}{max(len(df) + 1 + blank_rows, 2)}"
def add_list_validation(ws, df, col, list_key, last_row):
    j = list(df.columns).index(col) + 1; L = get_column_letter(list(LISTS).index(list_key) + 1); n = len(LISTS[list_key]) + 1
    dv = DataValidation(type="list", formula1=f"=lists!${L}$2:${L}${n}", allow_blank=True); dv.error = f"Pick a value from the {list_key} list"; dv.errorTitle = "Not in list"
    ws.add_data_validation(dv); dv.add(f"{get_column_letter(j)}2:{get_column_letter(j)}{last_row}")

wb = Workbook(); ws = wb.active; ws.title = "README"
lines = [
    ("NBA development-history pilot — working copy (built 2026-11-03)", True),
    ("Source of truth: the CSV files in analysis/injury/development_pilot/ in the nba-predictions-revived repo. This workbook is for hand entry; it will be converted back to those CSVs.", False),
    ("", False), ("WHAT TO FILL IN", True),
    ("Yellow column headers mark input columns. Fill the sheets participation_records and research_log. Sheets pilot_sample, strata_summary and lists are reference only; do not edit them.", False),
    ("Dropdowns: record_type, claim_kind, source_type, confidence and player_name are validated against the lists sheet. person_id may be left blank; it is filled from player_name on import.", False),
    ("Rows 2-3 of participation_records are an EXAMPLE (Allen Iverson, not in the sample) showing the expected format. Delete them when you start, or leave them; they are removed on import.", False),
    ("", False), ("RULES", True),
    ("One row per dated participation record, never one label per player. Every row cites a source; nothing is filled from memory.", False),
    ("Unknown stays unknown: write a row only when a source supports it. claim_kind = explicit_absence only when a source states non-participation (for example, 'played only basketball in high school').", False),
    ("A biography that mentions football establishes participation; one that mentions only basketball does not establish basketball-only.", False),
    ("evidence_passage: the sentence or two, verbatim, that supports the row (under 300 characters). source_url: the link; for PDFs add the page in notes.", False),
    ("start / end / seasons_or_ages: ISO dates or season labels such as 2001-02, or ages if that is all the source gives. These columns are text, so Excel will not turn 2001-02 into a date.", False),
    ("confidence: confirmed = official or primary (media guide, federation, school, league record); reported = reputable secondary or an encyclopedia entry with a citation; weak = forum or unsourced.", False),
    ("prior_injury rows: only injuries dated before the 2018-19 season. Do not look up injuries from 2018-19 onward for any sampled player.", False),
    ("", False), ("PROCEDURE PER PLAYER (log it in research_log)", True),
    ("1. Note the start time. Cap is 20 minutes; stop at the cap and log what is still missing (stopped_at_cap = TRUE).", False),
    ("2. Source order: current or most recent team media guide (PDF; search the name); NBA.com and Basketball-Reference biography; Wikipedia, following its citations and citing the underlying source where possible; RealGM profile (pre-draft and overseas history); FIBA player page (national teams, youth tournaments); MaxPreps, 247Sports, Rivals or ESPN recruiting (high-school seasons, grassroots program); one targeted news search for other sports and for an AAU or circuit program.", False),
    ("3. Write one row per record found. Log minutes_spent, sources_checked (semicolon-separated), records_written and stopped_at_cap.", False),
    ("", False), ("THE SAMPLE", True),
    ("46 players: 8 per pathway stratum (A US multi-year college; B US one college season; C US no college; D international without US college; E international with US college), split by NBA debut year (early = 2019 or earlier), plus 6 from the unresolved stratum X (pathway not classifiable from systematic fields: pre-2005 college careers, overseas or Overtime Elite routes). Drawn with seed 20261103 from the 1,278 players who appeared in 2018-19 through 2025-26, using only country, college and ESPN college seasons; injuries were never consulted.", False),
    ("Two agreement subsets: blind_subset = TRUE (subset A) was researched by Claude first, but leads for five of those players were mentioned in conversation before your pass, so A is a weaker agreement test. blind_subset_b = TRUE (subset B) is untouched: Claude researches those 12 only after your pass and says nothing about them until then. Research every player the same way regardless of flags.", False),
    ("college_seasons_espn and related columns are provisional inputs to the strata (match_method says how the ESPN link was made); please confirm college seasons during research.", False),
]
for i, (t, bold) in enumerate(lines, 1):
    c = ws.cell(row=i, column=1, value=t); c.font = FB if bold else F; c.alignment = WRAP
ws.column_dimensions["A"].width = 120
ws2 = wb.create_sheet("pilot_sample"); write_table(ws2, sample, widths={"player_name": 24, "college": 28, "stratum": 22, "match_method": 22})
ws3 = wb.create_sheet("participation_records")
rec_text = ["start", "end", "seasons_or_ages", "source_date", "retrieved_on", "games_documented", "person_id"]
write_table(ws3, recs, input_cols=set(recs.columns), text_cols=set(rec_text), blank_rows=600,
            widths={"player_name": 26, "record_type": 20, "claim_kind": 16, "sport": 14, "level": 14, "organization": 28, "location": 18, "seasons_or_ages": 22, "detail": 40, "evidence_passage": 60, "source_url": 45, "source_type": 14, "notes": 30})
last = len(recs) + 1 + 600
for col, key in (("record_type", "record_type"), ("claim_kind", "claim_kind"), ("source_type", "source_type"), ("confidence", "confidence"), ("player_name", "player_name")): add_list_validation(ws3, recs, col, key, last)
ws4 = wb.create_sheet("research_log")
write_table(ws4, log, input_cols=set(log.columns), text_cols={"date", "person_id"}, blank_rows=120, widths={"player_name": 26, "sources_checked": 50, "notes": 40})
for col, key in (("player_name", "player_name"), ("stopped_at_cap", "true_false")): add_list_validation(ws4, log, col, key, len(log) + 1 + 120)
ws5 = wb.create_sheet("strata_summary"); write_table(ws5, strata, widths={"stratum": 24, "match_method": 24})
ws6 = wb.create_sheet("lists")
for j, (k, vals) in enumerate(LISTS.items(), 1):
    c = ws6.cell(row=1, column=j, value=k); c.font = FB; c.fill = HEAD
    for i, v in enumerate(vals, 2): ws6.cell(row=i, column=j, value=v).font = F
    ws6.column_dimensions[get_column_letter(j)].width = 24
wb.save(OUT); print("saved", OUT)
