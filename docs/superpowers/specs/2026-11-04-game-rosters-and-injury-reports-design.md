# Game Rosters & Official Injury Reports (D2 foundation, 2012–2026) — Design

*Draft for approval, 2026-11-04; revised the same day after two rounds of
external review (scheduled-start naming, unavailable-not-absent slot
outcomes, resolution measured on identities, roster-completeness checks, the
restore order, the manifest allow-list for the dependency rebuild, reason
agreement with the derivative; then immutable inventory generations,
placeholder normalisation before comparison, gamebook evidence in the
snapshot, the daylight-saving ambiguity flag, the explicit excluded filter
and complete-PDF fixtures). This is the first of the injury-analysis
slices agreed on 2026-11-03: a roster denominator from full box scores plus
the league's official injury reports as attested primary documents. It
publishes evidence tables only. It defines no injury episode, no star tier
and no trend; those belong to the analysis design that follows. It completes
the "D2 — injury evidence and roster eligibility" row of
[`2026-07-31-player-availability-foundation-design.md`](2026-07-31-player-availability-foundation-design.md)
for completed seasons; the mutable 2026-27 feed is a separate design.*

## Goal

Publish, for every regular-season, playoff and play-in game already in the
trusted `player_games` publication (ending seasons 2012–2026):

1. **who was on the game roster** — every player on the active list,
   whether or not he played, and every player on the inactive list — so that
   "rostered game opportunities" can be counted without inferring them from
   appearances or from injury listings; and
2. **when the game was scheduled to start** (UTC instant), so that report
   versions can be placed strictly before or after the scheduled start. The
   source gives the scheduled time, not the actual first-period start; an
   actual-start field would need separate evidence and is not published.

And publish, for ending seasons 2019–2026, **every version of the NBA's
official injury report** the league published (2018-12-17 through
2026-09-30), parsed from the PDFs themselves into one row per listing, with
each listing resolved to the repository's game identity, team and `person_id`
where the evidence allows, and left explicitly unresolved where it does not.

It deliberately does **not** say why an inactive player was inactive, does
not merge consecutive listings into injury episodes, does not classify
reasons into a taxonomy, and does not define a "final pre-tip" status. It
gives later analyses the attested inputs those steps need.

## Why these sources (verified 2026-11-03/04)

- `LeagueGameLog` (D1) lists only credited appearances. The league box score
  (`BoxScoreTraditionalV3`) lists the active roster with a `comment` for
  non-appearances (`DNP - Coach's Decision`, `DND - Injury/Illness`, …), in
  every era sampled (openers of 2012-13, 2018-19, 2025-26; 2025 Finals game 1).
  For game `0022500001` it holds 27 active players against 20 appearances
  and 9 injury-report listings.
- `BoxScoreSummaryV3` returns each team's `inactives` (person id, names,
  jersey) for the same four games and a 2021 play-in game, plus
  `gameTimeUTC`, `gameEt` and `gameCode`. `gameTimeUTC` is the **scheduled**
  start: for the 2025 Finals opener (`0042400401`) it reads
  `2025-06-06T00:30:00Z`, 8:30 PM ET, while the external review read an
  8:37 PM first-period start in the official gamebook
  (`statsdmz.nba.com/pdfs/20250605/20250605_INDOKC_book.pdf`). That host
  redirects to `www.nba.com`, which does not answer this machine, so the
  gamebook reading is the reviewer's and was not reproduced here.
  `BoxScoreSummaryV2` is deprecated by `nba_api` ("data may be missing for
  games on or after 4/10/2025") and returned a non-JSON body for
  `0042400401`; it is not used.
- The official PDFs sit on a public static host with no bot wall. Every
  sampled report since 2018-12-17 is still served; the sampled unpublished
  slots return an S3 `AccessDenied` 403 (243–263 bytes of XML). S3 gives the
  same answer for a key that does not exist and for one the caller may not
  read, so a 403 is recorded as *unavailable at fetch*, never as proven
  absence. Yesterday's note that older reports were missing was a
  file-naming error, corrected below.
- The PDFs have no ruled table that `pdfplumber` can lift directly, but word
  coordinates separate cleanly (`x_tolerance ≤ 1.5`), the header row is
  present on every page and fixes the column edges, and the title line
  carries the printed report time. Layout changed three times (columns and a
  generator change to GemBox between 2021-10-20 and 2024-01-31); the parser
  is therefore header-driven, not era-driven.
- The MIT-licensed sportsdataverse release `nba_stats_injury_report` is a
  parsed derivative of the same PDFs. It is pinned and used to measure
  parser agreement; it is never the published listing source.

## Scope

| Item | Value |
|---|---|
| Roster and scheduled-start seasons | ending seasons 2012–2026, exactly the 19,183 games in the `player_games` publication (17,888 regular, 1,258 playoff, 37 play-in) |
| Injury-report calendar window | slot dates 2018-12-17 through 2026-09-30 inclusive |
| Injury-report seasons | 2019–2026 by the version-season rule below |
| Game types for listings | listings for regular, playoff and play-in games resolve; preseason, All-Star, Summer League and postponed-game listings are retained unresolved |
| Release boundary | completed seasons only; no slot dated after 2026-09-30 and no 2026-27 game; extension is a separate design |
| Coverage claim | the files retrievable from the enumerated slot grids at acquisition time, every negative outcome recorded with its evidence and rechecked once; not proof that every report the league ever published survives |
| New dependency | `pdfplumber` (see the dependency section) |

Expected cost, serial and resumable: about 16 hours of stats.nba.com
requests, about 13 hours of slot probes plus about 5 hours for the recheck
pass over roughly 34,000 unavailable slots, about two hours per full parse pass
over 57,000 PDFs (`extract_words` takes 0.09–0.14 s per report), and roughly
a day of serial CPU for one publish cycle including the build's two
independent validations and the archive script's revalidations.

## Architecture

- `src/nba_data/box_rosters.py` — worklist from the `player_games`
  publication, `BoxScoreTraditionalV3` and `BoxScoreSummaryV3` request
  identities, pacing/retry, raw-cache validation, normalization of one game;
  import of browser-downloaded gamebooks and the roster-sample comparison
  records.
- `src/nba_data/injury_reports.py` — slot enumeration, S3 acquisition with
  controls and immutable inventory generations, the recheck pass, PDF
  validation, header-driven parsing of one report into listing rows.
- `src/nba_data/availability_build.py` — whole-dataset assembly, game/team/
  person resolution, gates, exception-ledger enforcement, derivative
  agreement, partition/manifest creation, independent validation,
  transactional publication through `publication.publish_tree`.
- `src/nba_data/availability_snapshot.py` — layout and rebuild validators
  for the archive/restore scripts; no second data contract.
- `scripts/audit_availability_sources.py` — deterministic Markdown/JSON
  review artifacts (roster consistency, parse status, resolution,
  derivative agreement) written to an explicit output directory.

Part A (rosters and scheduled starts) has no dependency on Part B. Part B's
resolution depends on Part A's game index and rosters. The plan must deliver
A before B; both are published by one build.

## Part A — box-score rosters and scheduled starts

### Worklist

The distinct `(game_id, season, game_type, game_date)` set of the current
`player_games` publication, read through its `_manifest.json`, whose SHA-256
is bound into this publication. No other game source is consulted; a game
absent from `player_games` is out of scope by definition.

### Endpoint identities

| File | `nba_api` class | Fixed parameters |
|---|---|---|
| `<game_id>.traditional.json` | `BoxScoreTraditionalV3` | `start_period=0`, `end_period=0`, `start_range=0`, `end_range=0`, `range_type=0`, timeout 90 s |
| `<game_id>.summary.json` | `BoxScoreSummaryV3` | timeout 90 s |

Two requests per game; 38,366 requests in total.

### Transport policy

Identical to the player-logs contract: `stats_nba.preflight_dns()` first;
serial requests at least 1.5 s apart (about 16 hours for the full worklist,
resumable); at most five attempts per identity with 5/10/20/40 s delays for
request exceptions, HTTP 429 and 5xx; numeric `Retry-After` honoured only up
to 120 s; no retry on other 4xx, schema-invalid success or a block page; an
identity is skipped only when both body and sidecar verify; a damaged pair
is a hard failure, never a refetch.

### Layout and sidecars

```text
data/raw/stats_nba/box_rosters/
  season=2012/
    0021200001.traditional.json
    0021200001.traditional.json.meta.json
    0021200001.summary.json
    0021200001.summary.json.meta.json
  ...
  season=2026/
```

Sidecars are written by `cache.publish_bytes` and bind body SHA-256 and
length plus `provider`, `endpoint`, `game_id`, `season`, `game_type` and the
fixed parameters above.

### Raw validators

Traditional: a JSON object with `boxScoreTraditional.gameId` equal to the
requested id; `homeTeamId`/`awayTeamId` equal to the two team objects'
`teamId`; each of `homeTeam` and `awayTeam` has an integer `teamId`, a
non-empty `teamTricode`, string `teamCity` and `teamName`, and a non-empty
`players` list whose entries have
an integer `personId`, string `firstName`, `familyName`, `comment`,
`jerseyNum`, `position` and a `statistics.minutes` string. Summary: a JSON
object with `boxScoreSummary.gameId` equal to the requested id, `gameCode`
of the form `YYYYMMDD/AAAHHH`, `gameTimeUTC` parsing as an ISO instant,
`gameEt` present, and each team object carrying `teamId`, `teamTricode`,
`teamCity`, `teamName` and an `inactives` list (possibly empty) whose
entries have an integer
`personId` and string names and `jerseyNum`. Extra fields are preserved in
the body and ignored; missing required fields or HTML fail validation.

## Part B — official injury reports

### Slot grids and file names

Reports are published to
`https://ak-static.cms.nba.com/referee/injury/<file>`. The file name
encodes the publication slot in US Eastern 12-hour time, and the scheme
changed with the cadence:

| Grid | Slot dates enumerated | File name | Slots per day | Observed |
|---|---|---|---|---|
| `hour` | 2018-12-17 → 2025-12-31 | `Injury-Report_YYYY-MM-DD_hhAM.pdf` / `_hhPM.pdf`, `hh` ∈ 12, 01–11 | 24 | three per game day (01PM, 05PM, 08PM) through 2021-10-13; all 24 hours on game days from 2021-10-14 (2024-01-31: 24 of 24, 1.87 MB) |
| `quarter` | 2025-12-01 → 2026-09-30 | `Injury-Report_YYYY-MM-DD_hh_mmAM.pdf` / `_hh_mmPM.pdf`, `mm` ∈ 00, 15, 30, 45 | 96 | all 96 on game days from 2025-12-22 (2026-01-10: 96 of 96, 7.6 MB) |

The grids overlap for December 2025 so the switch date is observed, not
assumed (2025-12-21 serves `_05PM` and `_12AM`; 2025-12-22 serves
`_05_30PM` and not `_05PM`). Every date in each range is enumerated,
including off-season dates; an unavailable outcome is recorded with its
evidence, not presumed. About 91,000 probes (90,912 enumerated slots) are
estimated to find roughly 57,000 PDFs (about 4.5 GB); the count is an
estimate from sampled days, not a measurement. The printed report time
inside an `hour`-grid file is `hh:30`; the derivative's `report_datetime`
follows that printed time, which is why its times end in `:30`.

### Transport policy

Serial GET requests at least 0.5 s apart with a fixed descriptive
`User-Agent` naming this project and a 60 s timeout; about 13 hours for the
full window, resumable. Outcomes:

- HTTP 200 whose body begins `%PDF` → archive body and sidecar.
- HTTP 403 whose body is the S3 `AccessDenied` XML (at most 1,024 bytes) →
  record the slot as `unavailable` in the inventory together with the
  evidence: fetch time (UTC), HTTP status, the response `Date`,
  `x-amz-request-id` and `x-amz-id-2` headers when present, body length,
  body SHA-256 and the body itself verbatim, so that what the host answered
  can be read later without trusting a digest alone. This means "unavailable
  at fetch". S3 returns the same 403 for a key that does not exist and for
  one the caller may not read, so the record is not proof of absence.
- Anything else (5xx, timeout, HTML, a 200 that is not a PDF, a 403 with a
  different body) → retry up to five attempts with 5/10/20/40 s delays, then
  stop with a hard failure. An `unavailable` record is never written for a
  non-403 outcome.

**Controls.** Three known-good control files with committed SHA-256s, one
per naming era (`Injury-Report_2018-12-17_05PM.pdf`,
`Injury-Report_2024-01-31_05PM.pdf`, `Injury-Report_2025-12-22_05_30PM.pdf`),
are requested at the start of every run, after every 200 probes, and once
more as a closing control before the final batch of fewer than 200 probes is
committed. A control that does not return its known body stops the run with
a hard failure, and the `unavailable` records gathered since the last good
control are discarded rather than persisted: outcomes are held in memory
between controls and written as an inventory generation (below) only after
the control that closes the batch succeeds. A 403 wave caused by a block or
an outage therefore cannot be recorded as missing reports.

**Recheck.** `fetch-injury-reports --recheck-unavailable` (optionally with
`--month`) re-requests every `unavailable` slot once more, on a later
calendar day and under the same controls. A slot that now serves a PDF is
archived and its inventory record becomes `present`, keeping the earlier
evidence under `history`; a slot that is still 403 keeps its record and
gains a second evidence entry. The build requires every `unavailable` slot
to carry at least two evidence entries from different calendar days, so no
negative result enters the publication unrechecked. A `present` slot is
never re-requested and its body is never replaced.

### Layout, inventory and sidecars

```text
data/raw/nba_injury_reports/
  pdfs/2018-12/Injury-Report_2018-12-17_05PM.pdf
  pdfs/2018-12/Injury-Report_2018-12-17_05PM.pdf.meta.json
  ...
  pdfs/2026-01/Injury-Report_2026-01-10_12_00AM.pdf
  inventory/2018-12/gen-0001.json
  inventory/2018-12/gen-0001.json.meta.json
  inventory/2018-12/gen-0002.json
  inventory/2018-12/gen-0002.json.meta.json
  ...
```

PDF sidecars bind SHA-256 and length plus `provider: nba_official`, `url`,
`grid`, `slot_date`, `slot_label` (the file-name time token) and the
validation result below.

**Inventory generations.** Inventories are immutable: a run never modifies
an existing inventory file. At every successful control checkpoint the run
writes the complete state of each month it touched — every enumerated slot
of that month known so far with `status` (`present` or `unavailable`), file
name, for present slots the body SHA-256 and length, for unavailable slots
the evidence entries described above, and the control results of the runs
that contributed — as the next generation `inventory/YYYY-MM/gen-NNNN.json`
through `cache.publish_bytes`, numbered above every existing file of that
month. The current state of a month is its highest-numbered generation whose
body and sidecar verify. `publish_bytes` replaces the body and the sidecar
in two steps, so an interruption between them leaves one unverifiable
generation; the fetcher reports it, ignores it, and re-probes whatever that
generation would have added (present slots are never re-requested because
their PDFs are on disk), writing the next number. Nothing deletes a
generation automatically; the archive script refuses a raw tree containing
an unverifiable pair, so a damaged generation is removed by hand and the
removal is noted in the audit before the snapshot is taken. The build
consumes and binds, per month, exactly one generation — the highest, which
must verify — and requires every enumerated slot in the window to appear in
it with a recorded outcome and every unavailable slot to have been
rechecked, so a partial acquisition cannot publish. Superseded generations
stay on disk and in the snapshot but are not bound.

### PDF validation at fetch

A 200 body beginning `%PDF` is always archived; it is what the URL served.
Validation then records in the sidecar whether the body opens with
`pdfplumber` with at least one page (`opens`) and whether page one carries a
title `Injury Report: MM/DD/YY hh:mm AM|PM` whose date equals the slot date
and whose printed time falls inside the slot window: the same clock hour for
the `hour` grid, the fifteen minutes from the slot start for the `quarter`
grid (`title_check`: `ok`, `failed` or `unparsed`). The window rather than
equality is the rule because the league prints the generation time:
`Injury-Report_2026-04-30_01_15PM.pdf` prints `01:20 PM` and
`Injury-Report_2021-07-05_10PM.pdf` prints `10:37 PM`, both valid reports.
A body that does not open, or whose title check fails, stays archived with
that result; the build fails unless the exception ledger records a decision
for that file (`exclude`, or `accept_title` for a body that parses but whose
printed time sits outside its window). Only a 200 body that does not begin
`%PDF` takes the retry-then-stop path above, so a single damaged file cannot
stall the acquisition.

### Parsing contract

Parsing is header-driven so that one parser covers every layout:

1. **Header.** Every page must contain the header row. Required columns:
   `Game Date`, `Game Time`, `Matchup`, `Team`, `Player Name`,
   `Current Status`, `Reason`. Optional: `Category` (2018-19 layout) and
   `Previous Status` (2018-19 and 2019-20 layouts). Column order is taken
   from the header, not assumed; the three observed orders are
   `… Player Name, Category, Reason, Current Status, Previous Status`,
   `… Player Name, Current Status, Reason, Previous Status` and
   `… Player Name, Current Status, Reason`. The left edge of each header
   label fixes its column's left boundary; the last column runs to the page
   edge.
2. **Words.** `extract_words` with `x_tolerance` at most 1.5 (the default
   3.0 fuses adjacent cells such as `MinnesotaTimberwolves`). Each word
   below the header and above the `Page i of N` footer is assigned to the
   column containing its left edge. Words joined by a space within a column
   on one text line form a cell.
3. **Rows.** A row is anchored by a cell in `Player Name` or by a
   placeholder. Three placeholder kinds exist and the set is closed:
   `NOT YET SUBMITTED` (a team that has not filed; every layout),
   `ALL PLAYERS AVAILABLE` (positive evidence of full availability; 2018-19
   layout, page 3 of `Injury-Report_2018-12-17_05PM.pdf`), and the
   `Non-NBA Team` row printed for exhibition and All-Star opponents, whose
   other cells read `,` and `-` with matchups such as `UNK@HOU` and `UNK@UNK`
   (`Injury-Report_2019-09-30_01PM.pdf`, `Injury-Report_2019-02-15_01PM.pdf`).
   A placeholder row publishes its phrase in `placeholder_raw` and nulls in
   the player, status, reason, category and previous-status columns; the
   `Non-NBA Team` kind is recognised by that team string together with a
   `Player Name` cell of exactly `,`. Any other anchor without a player name
   fails the report. Cells in `Game Date`, `Game Time`, `Matchup` and `Team`
   are carried forward: a value seen on a text line applies to the first
   anchor at or below that line and to following anchors until replaced (the
   PDF prints them once per group, sometimes on the line above the first
   player). A `Team` cell may itself wrap over two lines in the pre-GemBox
   layouts (`Minnesota` / `Timberwolves`) and is joined with a space.
   Multi-line `Reason` (and `Category`) text is attached to the anchor
   nearest in vertical position; the generator centres a two-line reason
   around its player row, so the continuation lines fall within one line
   height above and below the anchor. Builders may use the horizontal rules
   present in the pre-GemBox layouts as row bands instead, provided the same
   gates pass.
4. **Gates (fail closed per report).** Header found on every page with all
   required columns; no word left unassigned; every anchored row has a
   non-null carried `Game Date`, `Game Time`, `Matchup` and `Team`;
   `Game Date` matches `MM/DD/YYYY`; `Game Time` matches `hh:mm (ET)`;
   `Matchup` matches `^[A-Z]{2,3}@[A-Z]{2,3}$` (`UNK` marks a non-NBA
   side); every non-placeholder status
   is one of `Available`, `Probable`, `Questionable`, `Doubtful`, `Out`
   verbatim; footer page numbers agree with the page count; the title
   parses. A report failing any gate gets `parse_status = failed` and the
   build stops unless the ledger excludes that report with a note.
5. **Preserved verbatim.** `player_name_raw`, `team_raw`, `matchup_raw`,
   `game_time_raw`, `current_status_raw`, `reason_raw`, `category_raw`,
   `previous_status_raw`. The reason text is not split into label, body
   part and detail here; the audit lists the distinct prefixes before the
   first ` - ` so the analysis design can fix a taxonomy against evidence.

### Derivative pin (cross-check only)

`python -m nba_data fetch-injury-derivative` downloads the eight
`injury_report_<season>.parquet` assets for 2019–2026 from the
`nba_stats_injury_report` release of `sportsdataverse/sportsdataverse-data`
into `data/raw/sportsdataverse/nba_stats_injury_report/` with sidecars
recording the release asset `updated_at` and SHA-256. Versions are joined on
the derivative's `report_datetime` equal to our `report_time_printed_et`.
The derivative holds a subset of slots (10 of the 24 hourly versions of
2024-01-31) and its timestamp is not always the printed title (`21:55`
against a printed `10:37 PM` on 2021-07-05), so versions unmatched on either
side are counted and listed, not treated as errors. For matched versions the
build first maps both sources into one row representation
`(game_date, matchup, team, kind, player_name, status, reason, category,
previous_status)`, where `kind` is `player` or the placeholder phrase. For
our rows the phrase comes from `placeholder_raw`; for the derivative it
comes from its `reason` column when `report_player_name` and
`current_status` are both null and `reason` is one of the three phrases —
the derivative stores `NOT YET SUBMITTED` there (in the cached 2026 asset,
33,327 rows in 3,175 of its 3,439 versions), and a bare comparison of
`reason` would flag every one of those as a disagreement. A derivative
null-status row that is none of the three phrases is reported as a
derivative artifact (the `,` rows seen in 2019 and 2020), not as our
disagreement. The build then compares the multiset of those rows — with
`category` and `previous_status` only for the layouts that carry them —
after collapsing whitespace, and records per season, in the audit and
manifest, rows agreeing on every field, rows agreeing on all but `reason`,
rows only in ours and rows only in theirs. Disagreements are grouped by
pattern — the set of differing fields and the normalised difference, such
as `reason: ours "Left Ankle; Sprain" / theirs "Left Ankle;"` — with a
count and every affected `report_id` per pattern, so the review reads
patterns rather than tens of thousands of lines while keeping each report
traceable. The reason comparison is the
check that a wrapped continuation line was attached to the right player;
player-and-status agreement alone cannot see that error. Agreement is not a
gate, and a difference never resolves in the derivative's favour
automatically: the derivative is itself a parse. The release review inspects
every disagreeing version against the rendered PDF before publication and
records the finding (ours right, theirs right, both wrong) in the audit.

## Normalized tables

### Publication layout

```text
data/parquet-large/availability/
  _manifest.json
  season=2012/game_rosters.parquet
  season=2012/game_schedule.parquet
  ...
  season=2019/game_rosters.parquet
  season=2019/game_schedule.parquet
  season=2019/injury_report_versions.parquet
  season=2019/injury_report_listings.parquet
  ...
  season=2026/
```

Rosters and schedule: 15 partitions each; report versions and listings: 8
each; 46 Zstandard Parquet files plus the manifest. Every partition has a
Parquet SHA-256 and a logical-row SHA-256 independent of Parquet bytes.

### `game_rosters` — primary key `(game_id, person_id)`

| Column | Arrow type | Nullable | Meaning |
|---|---|---:|---|
| `game_id` | `large_string` | no | NBA Stats game ID |
| `person_id` | `int64` | no | NBA Stats player ID |
| `season` | `int64` | no | Ending season (from `player_games`) |
| `game_type` | `large_string` | no | `regular`, `playoff`, `play_in` |
| `game_date` | `date32[day]` | no | From `player_games` |
| `nba_team_id` | `int64` | no | Source `teamId` |
| `team_tricode_raw` | `large_string` | no | Source `teamTricode` |
| `franchise_id` | `large_string` | no | Resolved through the game's `player_games` rows |
| `is_home` | `bool` | no | Team is `homeTeam` in the body |
| `roster_status` | `large_string` | no | `played`, `active_not_played`, `inactive` |
| `first_name_raw`, `family_name_raw` | `large_string` | no | Verbatim |
| `jersey_num` | `large_string` | yes | Source string with surrounding whitespace stripped; null when empty |
| `starter_position_raw` | `large_string` | yes | Source `position` for active players (`F`, `G`, `C`; non-empty means starter); null for inactives |
| `comment_raw` | `large_string` | yes | Source `comment` for active players; null when empty and for inactives |
| `comment_code` | `large_string` | yes | Text before the first ` - ` in `comment_raw` (`DNP`, `DND`, `NWT`, …), the whole comment when there is none |
| `minutes_raw` | `large_string` | yes | Source `statistics.minutes` verbatim for active players (`45:15`, empty → null) |

Rows ordered by `season`, `game_date`, `game_id`, `nba_team_id`,
`roster_status`, `person_id`. `roster_status` is `inactive` for summary
inactives, `played` for a traditional player whose `(game_id, person_id)`
exists in `player_games`, and `active_not_played` otherwise. Box-score
statistics are not republished; `player_games` already holds them.

### `game_schedule` — primary key `game_id`

| Column | Arrow type | Nullable | Meaning |
|---|---|---:|---|
| `game_id` | `large_string` | no | |
| `season`, `game_type`, `game_date` | as above | no | From `player_games` |
| `scheduled_start_utc` | `timestamp[us, tz=UTC]` | no | Parsed `gameTimeUTC`: the league's scheduled start, not the actual first-period start |
| `scheduled_start_et_raw` | `large_string` | no | Source `gameEt` verbatim (the trailing `Z` is not UTC) |
| `game_code_raw` | `large_string` | no | Source `gameCode` verbatim |

No actual tip-off instant is published: the box-score endpoints do not carry
one, and the gamebooks that do are a separate source.

### `injury_report_versions` — primary key `report_id`

| Column | Arrow type | Nullable | Meaning |
|---|---|---:|---|
| `report_id` | `large_string` | no | File name without `.pdf` |
| `season` | `int64` | no | Version-season rule below |
| `grid` | `large_string` | no | `hour` or `quarter` |
| `slot_date` | `date32[day]` | no | From the file name |
| `slot_label` | `large_string` | no | File-name time token (`05PM`, `12_00AM`) |
| `report_slot_et` | `timestamp[us]` | no | Naive US/Eastern slot start (`hh:00` for `hour`, exact for `quarter`); acquisition metadata, not a publication time |
| `report_time_printed_et` | `timestamp[us]` | yes | Naive US/Eastern time from the title line; null when the title did not parse. Retained for an excluded version whose title parsed, so consumers filter on `parse_status`, never on a null time |
| `report_time_printed_utc` | `timestamp[us, tz=UTC]` | yes | `report_time_printed_et` localised with `America/New_York` (zoneinfo, daylight-saving aware); null when the title did not parse and null when the printed time is ambiguous (below). A time inside the skipped 2 AM hour of the March switch day fails the title check |
| `report_time_printed_utc_ambiguous` | `bool` | no | True when the printed time falls in the repeated 1 AM hour of the November switch day, where the same printed time names two instants one hour apart (the printed time read at UTC−4 and at UTC−5); `report_time_printed_utc` is then null rather than a `fold` guess, and the audit counts these versions. False otherwise |
| `url` | `large_string` | no | |
| `sha256` | `large_string` | no | Body SHA-256 |
| `bytes` | `int64` | no | |
| `pages` | `int64` | yes | Null when the body did not open |
| `listing_rows` | `int64` | yes | Player rows parsed; null for excluded versions |
| `placeholder_rows` | `int64` | yes | Rows of any placeholder kind; null for excluded versions |
| `games_listed` | `int64` | yes | Distinct `(game_date, matchup)`; null for excluded versions |
| `games_resolved` | `int64` | yes | Of those, resolved to a `game_id`; null for excluded versions |
| `parse_status` | `large_string` | no | `ok`, `ok_title_accepted` (ledger `accept_title`) or `excluded` (ledger `exclude`) |
| `identical_listings_to_previous` | `bool` | no | Same listing multiset as the previous parsed version in slot order within the season; false for the first parsed version and for excluded versions |

Only present slots become versions; excluded versions remain rows so the
archive and the publication enumerate the same files. Unavailable slots live
in the inventory and in the manifest's per-grid totals.

### `injury_report_listings` — primary key `(report_id, row_number)`

| Column | Arrow type | Nullable | Meaning |
|---|---|---:|---|
| `report_id` | `large_string` | no | |
| `season` | `int64` | no | Version season |
| `row_number` | `int64` | no | 1-based document order across pages |
| `page` | `int64` | no | |
| `game_date` | `date32[day]` | no | Printed game date |
| `game_time_raw` | `large_string` | no | e.g. `07:00 (ET)` |
| `matchup_raw` | `large_string` | no | e.g. `MIL@DET` |
| `away_tricode`, `home_tricode` | `large_string` | no | Split from `matchup_raw` |
| `team_raw` | `large_string` | no | e.g. `Detroit Pistons` |
| `is_placeholder` | `bool` | no | Row carries no player listing (see the placeholder kinds in the parsing contract) |
| `placeholder_raw` | `large_string` | yes | Verbatim placeholder phrase: `NOT YET SUBMITTED`, `ALL PLAYERS AVAILABLE` or `Non-NBA Team`; null for player rows |
| `player_name_raw` | `large_string` | yes | Null for placeholders |
| `current_status_raw` | `large_string` | yes | Null for placeholders |
| `reason_raw` | `large_string` | yes | Joined continuation lines with single spaces |
| `category_raw` | `large_string` | yes | 2018-19 layout only |
| `previous_status_raw` | `large_string` | yes | 2018-19 and 2019-20 layouts only |
| `game_id` | `large_string` | yes | Resolved game |
| `game_resolution` | `large_string` | no | `resolved` or `no_box_score_game` |
| `nba_team_id` | `int64` | yes | Resolved team |
| `franchise_id` | `large_string` | yes | |
| `person_id` | `int64` | yes | Resolved player |
| `person_resolution` | `large_string` | no | `game_roster`, `season_roster`, `unresolved_no_match`, `unresolved_ambiguous`, `placeholder`, `unresolved_game` |

Rows ordered by `season`, `report_slot_et`, `report_id`, `row_number`.

## Resolution rules

- **Version season.** By slot date: 2019 = 2018-12-17 → 2019-09-30;
  2020 = 2019-10-01 → 2020-11-30 (the bubble Finals ended 2020-10-11);
  2021 = 2020-12-01 → 2021-09-30; 2022–2026 = 1 October → 30 September.
  Gate: every resolved game in a version has that season.
- **Game.** `(game_date, away_tricode, home_tricode)` against the index built
  from Part A bodies (`gameCode` date and tricodes, cross-checked with
  `player_games` date and the traditional body's tricodes). A key must match
  at most one game; no match → `no_box_score_game` and null `game_id`
  (preseason, All-Star, Summer League, postponed). Both tricodes must also
  belong to the two teams of the matched game.
- **Team.** `team_raw` must equal exactly one of the game's
  `teamCity + " " + teamName` strings (`LA Clippers` is city `LA`); any other
  outcome for a resolved game fails closed into the ledger.
- **Person.** Names are normalized (NFKD, accents stripped, casefolded,
  periods/hyphens/apostrophes/spaces removed) and compared as
  `family, given` against the resolved game's roster rows for that team
  (`game_roster`), then against every roster row of that `nba_team_id` in the
  version season (`season_roster`). Two or more candidates →
  `unresolved_ambiguous`; none → `unresolved_no_match`; listings for
  unresolved games → `unresolved_game`. Resolution is measured two ways: on
  listing rows, and on distinct listed identities
  `(game_id, nba_team_id, normalised name)`, because hourly re-publication
  repeats the same rows and can make a row rate look excellent while
  particular players stay unresolved all season. Gate: within each season at
  least 98 % of non-placeholder listing rows for resolved games and at least
  98 % of distinct listed identities resolve to a person. The audit and
  manifest report both rates per season, the identity rate by team, by
  calendar month and by status, and every unresolved name with its row and
  identity counts. The derivative's `person_id` is compared where both
  resolve and reported as agreement, not used.
- **Listed but not rostered.** A listing resolved through `season_roster`
  names a player who is on neither list of that game's box score. Season
  identity does not establish game eligibility, so these rows keep
  `person_resolution = season_roster` and are enumerated per season, team
  and status in the audit and counted in the manifest; those whose status is
  anything but `Out` are listed individually, because a player expected to
  dress who is missing from both lists points to a roster omission or a
  same-day transaction. Whether such a player counts as a rostered
  opportunity is the analysis design's decision; this publication makes the
  population visible.

## Whole-dataset validation

- **Coverage.** Every worklist game has exactly one validated traditional
  and one validated summary body; every enumerated slot has an inventory
  outcome; every unavailable slot has been rechecked; every present slot is
  parsed or ledger-excluded.
- **Appearance consistency.** Every `player_games` row has a traditional
  player row with the same `(game_id, person_id)` and team; every traditional
  player with an empty `comment` is `played`; every player with a non-empty
  `comment` is `active_not_played`; nobody is both active and inactive in one
  game; both bodies name the same two `teamId`s as the game's `player_games`
  rows. Violations fail closed unless listed in the ledger.
- **Roster completeness.** The appearance checks prove that nobody who
  played is missing; they cannot see a player who never appeared and was
  left off both lists. Two checks address that. First, active and inactive
  counts per team-game are summarised per season in the audit; every
  team-game with fewer than 8 or more than 15 active players, or with an
  empty inactive list, is enumerated and the counts enter the manifest.
  None is a gate, because league rules and hardship periods vary, but every
  listed case is reviewed. Second, a roster sample of at least 30 games is
  compared line by line with the official gamebooks
  (`statsdmz.nba.com/pdfs/YYYYMMDD/YYYYMMDD_AWAYHOME_book.pdf`): two games
  per season plus targeted cases — a game with a player in a season-long
  absence, a game within two days of a trade involving a listed player, a
  game with two-way players, a team-game with an empty inactive list, and a
  2020 bubble game. That host redirects to `www.nba.com`, which does not
  answer this machine, so the sample PDFs are downloaded by the project
  owner in a browser and imported with `import-gamebooks`, which checks the
  `%PDF` signature, derives the `game_id` from the file name's date and
  tricodes against the Part A index, and writes
  `data/raw/nba_gamebooks/<game_id>.book.pdf` with a `publish_bytes`
  sidecar recording URL, SHA-256, length, retrieval date and
  `retrieved_by: browser`; where no gamebook exists for a sampled game the
  audit says so and the next game is taken. The comparison extracts the two
  teams' active and inactive names from the gamebook's box-score page with
  `pdfplumber`, compares them with `game_rosters`, and writes one record per
  game to `data/raw/nba_gamebooks/comparisons/<game_id>.json` (gamebook
  names per team and list, roster names, differences, and the reviewer's
  note on each difference after looking at the rendered page), also through
  `publish_bytes`. The PDFs, sidecars and comparison records are bound by
  the manifest and travel in the snapshot, so recovery keeps the evidence
  behind the denominator and not only the tables. The audit summarises the
  sample with every difference; an unexplained difference blocks release. The gamebooks also record the
  actual first-period start, which the audit notes against
  `scheduled_start_utc` for the sampled games without publishing it.
- **Schedule.** `gameEt`'s date, `gameCode`'s date and `player_games`'
  `game_date` agree; `gameCode` tricodes equal the traditional body's.
- **Parsing.** Per-report gates above; the manifest records counts by
  `parse_status`, title-check result and grid.
- **Resolution.** The 98 % row and identity floors per season; team
  resolution exact; version-season agreement exact; listed-but-not-rostered
  rows enumerated.
- **Derivative agreement.** Reported per season (versions compared, rows
  agreeing on every field, rows agreeing on all but `reason`, rows only in
  ours, rows only in theirs, versions with any disagreement); every
  disagreeing version is enumerated in the audit with its inspection
  finding.

### Exception ledger

`src/nba_data/reference/availability_exceptions.json` has `schema_version`
and four sections, each a keyed object with exact typed fields and a
non-empty `note`, validated like the D1 ledgers (duplicate members, extra or
missing fields, type drift, key/body disagreement and stale or unlisted
entries all fail):

| Section | Key | Effect |
|---|---|---|
| `appearance_consistency` | `season\|game_id\|person_id` | Accept one exact comment/appearance disagreement with the raw `comment`, `minutes` and appearance fact recorded |
| `report_versions` | `report_id` | `action` is `exclude` (a report that does not open or fails a parse gate or title check; records the failed check and the body SHA-256) or `accept_title` (a report whose body parses but whose printed time lies outside its slot window; records printed time, slot and body SHA-256; its listings are published) |
| `team_resolution` | `report_id\|row_number` | Accept an exact `team_raw` that matches neither team string, with the intended `nba_team_id` |
| `schedule_dates` | `game_id` | Accept an exact `gameEt`/`gameCode`/`game_date` disagreement |

The initial committed ledger has four empty sections. Observed and approved
sets must be equal in both directions.

## Manifest and publication

`_manifest.json` is deterministic, sorted JSON without timestamps or
absolute paths. It records `schema_version`; the ordered Arrow schema of
each table; scope (seasons, game types, slot window, grids); dependency
versions including `pdfplumber`, `pdfminer.six` and `pypdfium2`; every
consumed raw body and sidecar (traditional, summary, PDF, the one inventory
generation per month with its generation number, derivative, gamebook PDFs
and comparison records) by relative path, SHA-256 and length, following the
play-by-play manifest, which already binds about 35,000 game bodies; the
`player_games` `_manifest.json` SHA-256 as worklist and appearance
reference, with the name of the NAS snapshot that holds that exact
publication; the three control files' SHA-256s; the ledger binding;
per-partition path, Parquet SHA-256, length, row count and logical-row
SHA-256; and the validation summary (coverage counts, consistency counts
and applied ledger digests, slot totals per grid as `present` and
`unavailable` with recheck counts, parse-status and title-check counts,
placeholder counts by phrase, per-season row and identity resolution rates,
listed-but-not-rostered counts, roster-size outlier counts, per-season
derivative agreement including reason agreement with the unmatched versions
on each side).

Builds stage in a sibling directory, reread every staged file, recompute
every manifest value, and publish through `publication.publish_tree`.
`validate_publication` walks the published tree without following symlinks,
requires exactly 46 partitions plus the manifest, rereads every partition,
re-attests every bound raw pair and the ledger, recomputes the gates against
the committed `player_games` publication, and rejects any manifest value it
cannot reproduce.

## Commands

```bash
# Networked, serial, resumable.
python -m nba_data fetch-box-rosters                   # all 19,183 games
python -m nba_data fetch-box-rosters --season 2019 --season 2026
python -m nba_data fetch-injury-reports                # whole slot window
python -m nba_data fetch-injury-reports --month 2024-01 --month 2024-02
python -m nba_data fetch-injury-reports --recheck-unavailable   # second pass over 403 slots
python -m nba_data fetch-injury-derivative             # pin the eight release assets

# Offline import of browser-downloaded gamebooks for the roster sample.
python -m nba_data import-gamebooks /path/to/downloads

# Offline review artifacts to an explicit directory.
python scripts/audit_availability_sources.py --data-dir data --output-dir /tmp/availability-review

# Strictly offline deterministic publication and independent validation.
python -m nba_data build-availability
```

`--season` and `--month` are repeatable for fetch commands and rejected for
build. Outside a source checkout `--data-dir` precedes the subcommand.

## Immutable snapshot and recovery

```text
/mnt/nas/llm/documents/nba-predictions-archive/
  availability-archive-2026-<first-12-hex-of-manifest-sha256>/
    raw/stats_nba/box_rosters/
    raw/nba_injury_reports/
    raw/sportsdataverse/nba_stats_injury_report/
    raw/nba_gamebooks/
    parquet-large/availability/
    SHA256SUMS
```

`scripts/archive_availability_snapshot.sh` and
`scripts/restore_availability_snapshot.sh` follow the player-games archive
contract (immutable final name, same-filesystem staging, sorted checksum
inventory, independent validation before and after copy, refusal to
overwrite, no symlink traversal). The snapshot is about 6 GB; the NAS has
3.1 TB free. Defaults are overridable through
`NBA_AVAILABILITY_ARCHIVE_ROOT` and `NBA_AVAILABILITY_DATA_DIR`.

**Restore dependency.** Validation reads the exact `player_games`
publication, which lives under the git-ignored `data/parquet-large/` and is
not inside this snapshot. The availability manifest therefore records the
D1 snapshot name (`player-games-archive-2026-<sha12>`, the one produced by
the dependency rebuild below) beside the `player_games` manifest digest,
and the documented recovery order is:

1. clone the repository at the recorded commit (the committed reference
   manifests under `data/parquet/` come with it);
2. `bash scripts/restore_player_games_snapshot.sh <that D1 snapshot>`;
3. `bash scripts/restore_availability_snapshot.sh <this snapshot>`, which
   refuses to proceed unless the restored `player_games/_manifest.json`
   hashes to the digest bound in `availability/_manifest.json`;
4. the independent validator (`python -m nba_data.availability_snapshot
   validate-layout`, mirroring `player_games_snapshot`), which the restore
   script also runs before and after the copy.

The release's restore test runs this sequence into an absent directory on
`/home` using only the `NBA_PLAYER_GAMES_*` and `NBA_AVAILABILITY_*`
overrides, never the working checkout's `data/`, and the audit records the
commands and the digests each step verified.

## Dependency change

`pdfplumber` becomes a direct requirement in `requirements.in` and a runtime
dependency in `pyproject.toml`. Pins resolved on 2026-11-04 with the
repository's `uv pip compile` flow, holding every existing pin:
`pdfplumber==0.11.10`, `pdfminer-six==20260107`, `pypdfium2==5.14.0`,
`cryptography==50.0.2` (new transitive dependency of pdfminer). The packages
are already installed in `.venv`; the requirement files were restored to the
committed state pending this approval.

`test_build_manifests_dependencies_match_direct_requirements` requires the
committed `build_manifest.json` and `historical_games_manifest.json` to list
every direct requirement with its installed version. Adding a requirement
therefore means regenerating those manifests (and `playoff_games_manifest.json`,
which pins the historical manifest) through the existing offline builds from
the cached raw sources, and then the `player_games` publication, which pins
`build_manifest.json` and `playoff_games_manifest.json`. The rebuild gate is
a manifest diff against an exact allow-list; every Parquet product must be
byte-identical and nothing outside the allow-list may change:

| Manifest | Allowed changes |
|---|---|
| `data/parquet/build_manifest.json` | `versions.dependencies` only |
| `data/parquet/historical_games_manifest.json` | `versions.dependencies` only |
| `data/parquet/playoff_games_manifest.json` | `versions.dependencies`; `sources.basketball_reference.historical_manifest.sha256` (it pins the historical manifest, `playoff_build.py`) |
| `data/parquet-large/player_games/_manifest.json` | `versions` (the installed-version block); `references.build_manifest.json` and `references.playoff_games_manifest.json` (`sha256`, `bytes`); nothing under `partitions`, `schema`, `scope`, `sources`, `validation` or `exception_ledger` |

The games, team-games and playoff Parquet files referenced by the
`player_games` manifest keep their digests, so only those two manifest pins
move. The play-by-play manifest pins none of these and is untouched. A new
`player-games-archive-2026-<new sha12>` snapshot (about 215 MB) is archived
beside the existing one, which is kept.

**Decision (2026-11-04, project owner, on the external review's
recommendation): the full ceremony.** The D1 contract made a NAS snapshot
with restore verification and a committed audit mandatory for a
publication, and the committed
`docs/audits/2026-08-02-player-games-source-audit.md` pins manifest
`73a6085a…` and snapshot `player-games-archive-2026-73a6085a7bb1`. The
rebuilt publication is archived, restore-verified into an absent target,
and a dated amendment is appended to that audit recording the
dependency-only rebuild, the manifest diff against the allow-list above, the
new manifest digest and snapshot name, and the byte-identical partitions;
the reviewed text stays intact. The alternatives considered — archiving
without restore verification or an amendment, or relaxing
`test_build_manifests_dependencies_match_direct_requirements` so manifests
may lag the direct set — were rejected as weakening a gate the repository
chose deliberately.

## Tests

- Box-roster cache: identity fields, incomplete pairs, checksum failure,
  schema drift (missing `inactives`, missing `comment`, mismatched
  `gameId`), block pages, retry budget, pacing, no-refetch.
- Box-roster normalization: `played`/`active_not_played`/`inactive`
  derivation, `comment_code`, jersey stripping, starter position, both-lists
  conflict, team-id agreement, scheduled-start parsing and date agreement,
  roster-size outlier enumeration.
- Slot enumeration: both grids, date ranges, December 2025 overlap, file-name
  rendering (`12AM`, `01PM`, `12_00AM`, `05_30PM`), resumability, `present`
  versus `unavailable` inventory records with their evidence entries,
  refusal to mark unavailable on a non-403 outcome, a failed control
  (including the closing control) discarding the unpersisted batch, the
  recheck pass turning an unavailable slot present while keeping its
  history, inventory generations (numbering above every existing file, the
  highest verifying generation as current state, a simulated interruption
  between the body and sidecar replacement leaving the previous generation
  current and the resumed run re-probing the lost batch, the build's refusal
  while the highest generation does not verify, the archive script's refusal
  of an unverifiable pair), verbatim 403 bodies in evidence entries, and the
  build's refusal to publish an unrechecked negative.
- PDF parsing against committed fixture PDFs that are **complete original
  files**, so the `Page i of N` footer gate and every other whole-report
  gate run end to end. The originals are small (the sampled files run from
  14 KB for a one-page report to 88 KB for ten pages; a one-page `pypdfium2`
  excerpt is larger at 61 KB because it embeds fonts, and its footer still
  reads `Page 1 of N`, so excerpts cannot pass the page-count gate). One per
  layout and one per special case, under `tests/fixtures/injury_reports/`
  with source URL and body SHA-256 recorded beside each:
  `Injury-Report_2018-12-17_05PM.pdf` (4 pages, 34 KB; 2018-19 layout,
  `ALL PLAYERS AVAILABLE`), `Injury-Report_2019-02-15_01PM.pdf` (1 page,
  14 KB; All-Star `Non-NBA Team`), `Injury-Report_2019-09-30_01PM.pdf`
  (1 page, 14 KB; 2019-20 layout, preseason `UNK` matchups),
  `Injury-Report_2021-04-01_05PM.pdf` (4 pages, 31 KB; seven-column
  pre-GemBox layout), `Injury-Report_2024-01-31_05PM.pdf` (7 pages, 80 KB;
  GemBox), `Injury-Report_2026-01-10_12_00AM.pdf` (5 pages, 75 KB; quarter
  grid) — about 250 KB in total. Excerpts, if the builder wants them, serve
  page-level parser tests only and never the whole-report gates. Cases:
  header detection
  and column order per layout, fused-cell prevention, carry-forward across
  pages and above-anchor team cells, wrapped two-line team cells, two- and
  three-line reasons, all three placeholder kinds including the
  `Non-NBA Team` rows, an unknown anchor failing the report, status
  vocabulary, footer agreement, title parsing, the slot-window rule
  (`01:20 PM` in a `01_15PM` file passes; a printed time in another hour
  fails), `report_time_printed_utc` on an EST date and an EDT date, the
  November repeated hour yielding a null instant with the ambiguity flag
  set, the March gap failing the title check, a report whose title parses
  but which fails another gate keeping its printed time while
  `parse_status` is `excluded`, an archived body that does not open, and
  each gate's failure path. Beside every fixture sits a
  hand-checked expected-rows CSV covering all rows of the excerpt, including
  a page-break continuation and a multi-line reason, and the parser output
  must equal it exactly.
- Resolution: game key uniqueness, tricode membership, team string matching
  (`LA Clippers`), name normalization (suffixes, accents, hyphens),
  game-roster then season-roster fallback, ambiguity, unresolved games,
  version-season rule including the bubble boundary, the row and identity
  floors measured separately (a repeated unresolved row failing the identity
  floor while passing the row floor), listed-but-not-rostered enumeration.
- Derivative agreement: placeholder normalisation (a derivative
  `NOT YET SUBMITTED` reason row equals our placeholder row; a derivative
  `,` row is reported as its artifact), classification of a matched
  version's rows into full agreement, all-but-reason agreement, only-ours
  and only-theirs, pattern grouping that keeps every affected `report_id`,
  and a wrapped reason attached to the wrong player being reported as a
  reason disagreement.
- Gamebook import and comparison: signature check, `game_id` derivation
  from the file name, sidecar fields, name extraction from a fixture
  gamebook page, difference reporting, and the manifest binding of PDFs,
  sidecars and comparison records.
- Ledger: exact-shape acceptance and every drift/stale/unlisted failure for
  all four sections, both `report_versions` actions, and the null version
  columns of an excluded report.
- Build: deterministic partitions and manifest from the same inputs, stage
  reread, rollback on failure, independent-validator tamper cases (partition,
  manifest value, raw body, inventory, ledger), partial inventory refusal.
- Dependency rebuild: the manifest-diff allow-list accepts exactly the
  listed key paths and rejects any other change or a changed Parquet digest.
- Archive/restore body, sidecar, manifest, checksum, symlink, existing-target
  and copy-time-race failures, and refusal to restore when the restored
  `player_games` manifest digest differs from the bound one.
- CLI: fetch/build separation, repeatable `--season`/`--month`, rejection on
  build.
- Repository policy: no file under `data/raw/` or `data/parquet-large/`
  tracked; requirement pins and wheel metadata include `pdfplumber`;
  regenerated manifests list it.

## Documentation

`README.md`, `docs/data-dictionary.md` (the four tables; resolution rules;
the recommended last-version-before-start derivation as a documented recipe:
drop versions with `parse_status = excluded` explicitly (some carry a parsed
title, so a null time is not the filter), take the remaining versions whose
`report_time_printed_utc` is strictly earlier than `scheduled_start_utc`
and keep the latest; a version with `report_time_printed_utc_ambiguous`
true has two candidate instants one hour apart, both between 05:00Z and
07:00Z on the switch day, and is usable only when both fall on the same
side of the start (true for every scheduled NBA start, which never falls in
that window) and otherwise unplaceable; `report_slot_et` is acquisition
metadata and no substitute, because a `01_15PM` file can be generated at
`01:20 PM`; and a printed generation time does not establish when the file
became publicly reachable, which this publication does not know; the explicit
statement that an inactive-list row carries no reason; and the statement
that `scheduled_start_utc` is the scheduled, not the actual, start), a
dated amendment appended to `docs/audits/2026-08-02-player-games-source-audit.md`
for the dependency rebuild, and a committed
`docs/audits/2026-11-<dd>-availability-source-audit.md` stating the
manifest SHA-256, the D1 snapshot name and digest it depends on, counts per
season and table, slot totals per grid with unavailable and recheck counts
and control results, parse and title-check outcomes, placeholder counts by
phrase, row and identity resolution rates with the team, month and status
breakdowns, listed-but-not-rostered counts, roster-size outliers and the
gamebook sample comparison, derivative agreement including reason agreement
with every disagreeing and unmatched version and the inspection finding for
each, applied ledger entries, the restore-test command sequence, and the
file-naming correction to the 2026-11-03 spike note.

## Out of scope

- Ending season 2027 and any mutable in-progress feed (next design).
- Injury episodes, games lost, final-pre-tip status, reason taxonomy,
  anatomy or mechanism coding, star or workload tiers, and any trend or
  causal analysis.
- Preseason, All-Star and Summer League box scores (their listings remain
  unresolved by design).
- Roster transactions, contracts, two-way assignments and suspensions as
  sources; `BoxScoreSummaryV2`; Pro Sports Transactions; the `cdn.nba.com`
  live box-score feed (403 from this machine).
- The actual first-period start instant; gamebooks are consulted only for
  the roster sample and nothing from them is published.
- The Wayback Machine, which holds about 14,700 of these PDFs, as a source;
  it is noted as a recovery option only.
- Any committed raw body, PDF or large Parquet file.

## Left to the builder

- Parser internals: ruled-band versus nearest-anchor row assembly, the
  tolerance for attaching continuation lines, and page-break handling, as
  long as the gates and fixtures pass.
- Exact name-normalization implementation and the candidate-set data
  structures.
- The JSON structure inside an inventory generation, request-pacing
  implementation, and memory strategy for a manifest binding about 100,000
  raw files.
- Module boundaries inside `availability_build.py`, fixture selection and
  test organisation.

## Success criteria

1. 38,366 raw box-score pairs verify; every enumerated slot in both grids has
   an outcome in its month's highest inventory generation, which verifies;
   every unavailable slot carries two evidence entries from different days
   with verbatim bodies; every control request, closing controls included,
   returned its known body; every present PDF is archived with a sidecar
   and a title-check result.
2. `game_rosters` and `game_schedule` cover all 19,183 games with zero
   unreviewed appearance or schedule disagreements; every `player_games` row
   is a `played` roster row; the roster sample against the gamebooks is
   recorded in bound comparison records with no unexplained difference.
3. Every present report is parsed or ledger-excluded; team resolution is
   exact; each season meets both resolution floors; listed-but-not-rostered
   rows are enumerated; derivative disagreements, reason disagreements
   included, are enumerated and reviewed.
4. Two offline builds from the same raw trees and ledger yield identical
   partition and manifest bytes; independent validation passes; the three
   regenerated reference manifests and the rebuilt `player_games` publication
   change only within the manifest allow-list, with every Parquet digest
   unchanged.
5. The NAS snapshot validates and restores into an absent target in the
   documented order after the D1 snapshot it names, using only documented
   prerequisites; the fresh clone size and tracking policy are unchanged.
