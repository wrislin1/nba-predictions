# Injury-source and current-season spike — findings

Date: 2026-11-03. Run from riz-llm using the repository `.venv` (Python 3.12,
nba_api 1.11.4). Revised the same day after an independent reassessment
corrected the first feasibility probe and after the D1 player-games branch
was merged. Probe inputs are cached under gitignored
`data/raw/spike/injury_report_2026-11-03/`; nothing under `data/parquet/`
changed. This note records what exists, what is reachable, what the available
data can and cannot say about the "injury crisis" question, and which
decisions remain. It approves no analysis.

## The question

The prompt is the early-2026-27 run of injuries (Holmgren, Jalen Williams,
Wallace, Edwards, Haliburton, Morant and others) and the discussion around it.
Three questions fall out:

1. **Is there actually a crisis?** Is the 2026-27 start unusual against the
   same calendar window in earlier seasons, measured as injury burden rather
   than as headlines?
2. **If so, what might cause it?** The hypotheses raised: more possessions and
   more distance run per game (style of play); overuse injuries (hamstring,
   calf, patellar tendon) rising relative to contact injuries; year-round,
   single-sport development in the United States versus other systems
   (a "USA" dummy in a regression); schedule and workload compression.
3. **Can we find it in our data?** Which of those variables the repository
   holds or can acquire, and at what provenance standard.

## Repository state

### `master` (a8af351, after merging `feat/player-games-workload`)

| Publication | Rows | Ending seasons | Last game date |
|---|---:|---|---|
| `games.parquet` / `team_games.parquet` | 49,117 / 98,234 | 1984–2026 regular | 2026-04-12 |
| `historical_games.parquet` | 19,405 | 1947–1983 | 1983-04-17 |
| `playoff_games.parquet` | 4,652 | 1947–2026 postseason | 2026-06-13 |
| `data/parquet-large/pbp/` (gitignored) | 18,303,908 events | 1997–2026, 30 partitions | per final audit |
| `data/parquet-large/player_games/` (gitignored) | 406,843 | 2012–2026, 15 partitions | per final audit |

Nothing from the 2026-27 season (ending season 2027) is collected. Every
modern-pipeline boundary is pinned to completed seasons: `stats_nba.SEASONS`
and `stats_nba.EXPECTED_GAMES` stop at 2026 with exact 1,230-game counts,
`__main__` restricts `fetch --season` to `2020..2026`, the hoopR pin
(`hoopr_snapshot.json`, commit `50fe7e42`) lists 2020–2026, and
`bref.MONTHS_BY_SEASON` ends at 2026. Every approved design states that a
mutable in-progress season is out of scope.

### D1 merge (2026-11-03)

The D1 player-games foundation, finished on `feat/player-games-workload`
(26 commits, +14,626 lines), was merged into `master` as a8af351. Before the
merge `master` and `origin/master` were both bcd49db; `git fetch origin`
timed out from this host, so the remote was not re-synchronised. The merge
conflicted only on three documentation files that both sides had added or
edited (the player-games plan, the player-games design, and the availability
foundation design); the branch versions carry the 2026-08-03 amendments and
were taken. All code, tests, scripts and reference ledgers merged cleanly.

The branch's raw player-log cache (93 attested pairs, 1984–2026) and the
15-partition publication existed only in the worktree's `data/` tree. Both
were hardlinked into the main checkout and are byte-identical there, and
`python -m nba_data.player_games_snapshot validate-layout` run from the main
checkout against the committed `data/parquet` references returned
`status: ok` with 93 archive entries, 37 publication entries, 15 partitions
and the audited manifest SHA-256 `73a6085a…`. The
gitignored SDD diagnostics referenced by the amendment were copied to the
main `.superpowers/sdd/` as well. The immutable NAS snapshot
`player-games-archive-2026-73a6085a7bb1` is unchanged.

Facts about D1 as merged: trusted publication 2012–2026, 406,843 rows,
19,183 games, 1,862 players, zero point or minute exceptions; 1984–2011
quarantined as source evidence, not released workload.

### Test status

- Branch suite at 4ae0613 (the merged tree): 1,528 tests passed in 11 m 42 s.
- `master` before the merge collected 1,199 tests; a full run reported
  1,169 passed, 24 failed, 6 errors. Every failure inspected was the
  play-by-play archive or restore test family refusing at the archive
  script's capacity gate (`required = source + destination_total / 5`),
  because the tests archive into pytest's temp directory on the 210 GB root
  filesystem, where the rule demands 42 GB free. Root availability was
  observed between 27 GB and 54 GB during the day. The same rule exists in
  the player-games archive script, so both suites are sensitive to free
  space on `/`. The scripts and tests are identical on both sides of the
  merge, so this is an environmental condition, not a regression.
- Merged `master` (a8af351): `scripts/verify_repository.py` passes (181
  tracked files, 54.7 MiB `.git`), and the full suite run from the main
  checkout passed 1,528 tests in 11 m 40 s with 42 GB free on `/` at the
  start. Running two suites concurrently on this host, or running while
  other work consumes root space, is what produced the earlier capacity
  failures.

### Roadmap position

| Slice | Status on 2026-11-03 |
|---|---|
| D1 player appearances/workload | Merged (trusted 2012–2026; raw 1984–2026) |
| D3 schedule exposure | No design yet |
| D2 injury evidence + roster denominator | Source half addressed by this spike; framing decision open |
| Injury analysis | Brainstorm started 2026-11-03; no design yet |
| COVID fixed-effects causal analysis | Not started |

## Provider probes

### stats.nba.com through `nba_api` — WORKS for 2026-27

`stats_nba.preflight_dns()` returned a real Akamai address (no `0.0.0.0`
AdGuard answer). A single `LeagueGameLog` request (`Season=2026-27`,
`Regular Season`, team mode, 90 s timeout) completed in 0.5 s:

| Season | team rows | unique games | first date | last date |
|---|---:|---:|---|---|
| 2026-27 | 214 | 107 | 2026-10-20 | 2026-11-02 |

The independent reassessment also confirmed 2,413 player-game rows for the
season from the same endpoint in player mode and 448 player rows from the
speed/distance tracking summary; game-level completeness of tracking data is
not yet qualified.

Diagnostic note: the same URL requested with `curl` and an ad-hoc header set
hung until timeout. That is header-set sensitivity at the Akamai edge, not a
block; only the `nba_api` request shape is evidence of access. The host has
no working IPv6 route (`curl -6` fails instantly for any site); `nba_api`
falls back to IPv4 without issue.

### sportsdataverse/hoopR-nba-data — 2027 schedule already published

`nba/schedules/parquet/nba_schedule_2027.parquet` exists on `main`
(last commit 2026-11-03): 1,206 rows, 107 `STATUS_FINAL` through 2026-11-02,
1,099 scheduled through 2027-04-11, 4 neutral-site games. The completed count,
dates, teams and scores agree with the stats.nba.com probe. The repository
pin does not include 2027.

### basketball-reference — reachable

`leagues/NBA_2027_games-october.html` returned HTTP 200 in 0.26 s with a
browser User-Agent. Not parsed.

### Official NBA injury report PDFs — reachable, public S3, no bot wall

- Index page: `official.nba.com/nba-injury-report-2020-21-season` now lists
  the 2026-27 season; on 2026-11-03 it showed 49 PDFs, one every 15 minutes.
- URL pattern: `https://ak-static.cms.nba.com/referee/injury/<file>` with
  the slot encoded in 12-hour Eastern time. The file name changed with the
  cadence (verified 2026-11-04 by probing the live host): hour-only names
  `Injury-Report_YYYY-MM-DD_hhAM.pdf` / `_hhPM.pdf` from 2018-12-17 through
  2025-12-21 (three per game day, 01PM/05PM/08PM, until 2021-10-13; all 24
  hours on game days from 2021-10-14), then quarter-hour names
  `Injury-Report_YYYY-MM-DD_hh_mmAM.pdf` / `_hh_mmPM.pdf` from 2025-12-22
  (all 96 slots on game days). The printed title inside an hour-named file
  reads `hh:30`, which is why the derivative's `report_datetime` values end
  in `:30`. `HEAD` on the 2026-11-03 12:00 PM file returned HTTP 200,
  82,803 bytes, `Server: AmazonS3`. Unpublished slots return an S3
  `AccessDenied` 403. An earlier revision of this note gave the quarter-hour
  form as the only pattern; requests for older reports in that form 403 for
  the naming reason, not because the archive is incomplete. The Wayback
  Machine holds about 14,700 of these URLs (sparse before 2021-10, dense
  after), a recovery option rather than a source.
- Cadence history (per sportsdataverse): three per day through 2021-10-14,
  hourly through 2025-12-21, every 15 minutes since 2025-12-22. Archive
  begins 2018-12-17, so it covers the COVID-era seasons.
- Reporting rules on the page: 5 p.m. local the day before (not for the
  second night of a back-to-back); 1 p.m. local on the second night; game-day
  update 11 a.m.–1 p.m. local (8–10 a.m. for tips at or before 5 p.m.);
  continual updates thereafter.
- Full backfill scale (per hoopR-nba-stats-raw PR #18): about 2,753 dates and
  58,104 slot probes, roughly 13 hours at 0.5 s spacing; change-only storage
  reduces a season from about 17,000 slot files to about 180.

### sportsdataverse-data release `nba_stats_injury_report` — WORKS, MIT

Release in `sportsdataverse/sportsdataverse-data` (repository license MIT),
assets `injury_report_<ending-season>.{parquet,csv,rds}` for 2019–2027 plus
`timestamp.json` (`2026-11-03 09:59:05 EST`). It is a parsed derivative of
the official PDFs (sdv-py `nba_official_injury`, pdfplumber), one row per
listing per report version. Agreement between the derivative and the PDFs
checks parser accuracy; it is not independent medical confirmation.

Schema (17 columns): `season`, `report_datetime` (US/Eastern, naive),
`game_date`, `game_time`, `report_matchup`, `away_team_abbr`,
`home_team_abbr`, `team`, `report_player_name`, `current_status`, `reason`,
`category`, `previous_status`, `game_id`, `team_id`, `person_id`,
`match_method` (`exact` / `family` / `league` / `roster` / null).

| Ending season | rows | report versions | game dates | `Out` | `Questionable` | null status (not yet submitted) | `person_id` null |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2027 (to 2026-11-03) | 25,361 | 286 | 15 | 16,249 | 3,043 | 2,638 | 11.1 % |
| 2026 | 376,069 | 3,439 | 226 | 246,517 | 42,756 | 33,327 | 9.7 % |

Nearly all null `person_id` rows are the `NOT YET SUBMITTED` team
placeholders; genuine unmatched player names are under 1 %. `game_id` is
populated for every 2027 row and includes preseason (`001`), regular (`002`),
playoff (`004`) and play-in (`005`) prefixes. Regular-season `game_id`
values for 2019–2026 match the repository's `games.parquet` identities
exactly (100 % of distinct IDs in the probe windows).

Format drift a parser must handle: in ending season 2019 the injury label
lives in `category` (`Injury/Illness`) and `reason` is free text
(`Left ankle sprain`); from 2020 the label is prefixed into `reason`
(`Injury/Illness - Left Ankle; Sprain`) and `category` is mostly null; from
2027 the separator changes again (`Injury/Illness - Left Hamstring Strain`).
COVID protocol listings appear as `Health and Safety Protocols`. Standalone
`Rest` entries exist outside the injury label.

### Pro Sports Transactions — still blocked

The basketball search page returned HTTP 403 again, matching the 2026-08-02
finding. No workaround is proposed.

### Not probed

`cdn.nba.com` liveData returned 403 to `curl` and is not needed. Paid feeds
(BALLDONTLIE injuries tier, SportsDataIO, Sportradar) and the unofficial
RapidAPI listing were not evaluated; the first three are licensed products and
the last has unverified terms. `BoxScoreSummaryV3` inactive-player lists on stats.nba.com were not
probed here; the player-bio and draft-combine endpoints were (see the
development-history section).

## Quick feasibility probe — early-season injury burden (scratch, not validated)

Three versions of this probe exist under `data/raw/spike/` and are kept for the
record: v1 (denominator from report rows, no tip-off cutoff), v2 (schedule
denominator, printed-time cutoff inclusive at tip-off, a silently changed
two-season core definition) and v3, the current one. v3 answers a second
independent review of v2, which found that the printed `game_time` is 12-hour
without AM/PM so "11:00 (ET)" can mean 11 p.m., that the cutoff should be
strictly before tip-off, and that the core-player change needed to be shown
as a sensitivity. Script: `data/raw/spike/injury_quicklook_v3_2026-11-03.py`.

Method (v3): regular-season rows (`game_id` prefix `002`) for the first 14
calendar days from each season's first regular-season game date. Tip-off is
the pinned hoopR schedule's UTC timestamp converted to Eastern, joined through
the ESPN game IDs that `games.parquet` already carries for 2020–2026; for
2026-27 the hoopR 2027 schedule is matched on date and team abbreviations
through a six-entry ESPN-to-NBA abbreviation map derived empirically from
2025-26 (GS, NO, NY, SA, UTAH, WSH). 2018-19 has no pinned schedule and falls
back to the printed time with 1–11 o'clock read as p.m. Report versions at or
after tip-off are dropped (the inclusive variant is reported as a
sensitivity); the final remaining version per (game, team, player) is kept;
`Out` listings with an injury or illness label are counted, excluding
protocols and anything labelled management, rest or return-to-competition
(counted separately, including standalone `Rest`); the denominator is
scheduled team-games from `games.parquet` (2019–2026) or the hoopR 2027
completed games. "Core 150" is reported two ways: the 150 players with the
most previous-season regular minutes (core-1) and the 150 highest by the
maximum over the two previous seasons (core-2), both from the D1 publication;
they are sensitivity variants, not one cohort.

| Ending season | First game | Scheduled team-games | Team-games on report | Injury `Out` (strict) | Injury `Out` (inclusive) | Mgmt/rest `Out` | Injury `Out` per team-game | Distinct players `Out` | Core-1 per team-game | Core-2 per team-game |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2019 | 2018-12-17 (archive start, mid-season; printed-time fallback) | 198 | 200 | 364 | 366 | 11 | 1.838 | 94 | 0.732 | 0.753 |
| 2020 | 2019-10-22 | 192 | 192 | 305 | 307 | 15 | 1.589 | 84 | 0.438 | 0.443 |
| 2021 | 2020-12-22 (COVID restart, protocols excluded) | 200 | 199 | 342 | 343 | 8 | 1.710 | 83 | 0.435 | 0.470 |
| 2022 | 2021-10-19 | 204 | 204 | 362 | 370 | 14 | 1.775 | 94 | 0.549 | 0.627 |
| 2023 | 2022-10-18 | 204 | 204 | 373 | 379 | 25 | 1.828 | 102 | 0.544 | 0.510 |
| 2024 | 2023-10-24 | 206 | 204 | 340 | 347 | 7 | 1.650 | 98 | 0.471 | 0.417 |
| 2025 | 2024-10-22 | 216 | 216 | 465 | 471 | 18 | 2.153 | 109 | 0.611 | 0.648 |
| 2026 | 2025-10-21 | 206 | 206 | 486 | 491 | 27 | 2.359 | 122 | 0.903 | 0.850 |
| 2027 | 2026-10-20 | 214 | 214 | 486 | 486 | 57 | 2.271 | 131 | 0.519 | 0.607 |

Exact schedule tip-offs covered every 2020–2027 row; no row lacked a tip-off.
The strict-cutoff counts reproduce the second reviewer's independent figures
where quoted (2024-25: 465 listings; rates 1.59, 2.15, 2.36 and 2.27 for
2019-20, 2024-25, 2025-26 and 2026-27). One 2023-24 game in the window has no
report rows at all (206 scheduled against 204 covered).

What the probe suggests, subject to the caveats below:

- Injury `Out` burden in the opening fortnight has been higher for three
  seasons (2.15–2.36 per scheduled team-game in 2024-25 through 2026-27) than
  in 2019-20 through 2023-24 (1.59–1.83). The 2026-27 start is the
  second-highest of the nine windows, below 2025-26. (An earlier revision of
  this note said "third-highest"; that was an error carried from v1.)
- Weighted toward established players, 2026-27 sits mid-range on either core
  definition (0.52 and 0.61), and 2025-26 is the outlier on both (0.90 and
  0.85).
- `Out` listings labelled management or rest roughly doubled in 2026-27
  (57 against 27). Whether that is more load management or a labelling shift
  under the 65-game rule needs per-player follow-up.

None of this establishes that 2026-27 is statistically exceptional, and none
of it measures new injuries:

- These are absence listings, not incidence. Opening-fortnight listings mix
  injuries carried in from the off-season with newly sustained ones, and a
  season-ending injury in late October registers only its first few games.
- Listings are not severity. Forward games lost cannot be measured for the
  current season.
- The core sets are defined from seasons before the window, which avoids
  selecting on the outcome, but they still omit rookies and anyone who missed
  most of the defining seasons; they cannot support a general claim about
  star health.
- Roster rules changed (third two-way slot from 2023-24); two-way and
  assignment listings are excluded here but roster denominators are not
  modelled.
- The 2019 window is mid-season and 2021 is the COVID restart; both are
  shown for completeness only.
- Report cadence changed three times. The schedule-timestamp cutoff and
  final-version rule handle it here, but a repository parser must prove those
  choices against the raw PDFs.
- The derivative's name-to-`person_id` matching is roughly 99 % on player
  rows but is not independently attested.

## Prior literature

Verification status is noted per item; abstracts were retrieved through the
PubMed and Crossref APIs where the publisher pages were cookie-walled.

- **Game-loss injury rates were flat before COVID.** *Epidemiology of
  Injuries Among NBA Players: 2013-2014 Through 2018-2019* (Sports Health,
  2024; audited NBA Injury and Illness Database) reports rates of 5.6–7.0
  game-loss injuries per 10,000 player-minutes and states that "linear trend
  analyses did not suggest a change in game-loss injury rates from 2014-2015
  through 2018-2019". Verified from the article. A useful benchmark; it says
  nothing about 2026-27.
- **Rest is not a settled remedy.** *The Relationship Between Games Missed
  for Rest or Load Management and Injury in the NBA, 2014–15 Through 2022–23*
  (Sports Medicine, published 2026-05-30). Title and venue verified through
  Crossref; the finding of no significant association between rest games and
  later injury risk, with acknowledged residual confounding, is as described
  by the reassessment and not independently read here.
- **Multisport background and missed games.** *Effects of Early Sport
  Specialization on Injury Load Management and Athletic Success of NBA
  Players* (Orthop J Sports Med, 2025; 318 first-round picks 2013–2023):
  multisport high-school athletes missed 13.5 % of games to injury against
  16.9 % for single-sport athletes, and workload correlated with injuries only
  in the single-sport group. Verified from the abstract. Descriptive, with
  obvious selection concerns.
- **Youth year-round play.** *Injury Risk Factors Related to Sport
  Specialization in High School Basketball: A Prospective Study* (J Athl
  Train, 2024). Title verified; the reassessment reports year-round play
  associated with higher injury risk and the general specialization
  classification inconclusive.

Implication for the "USA" hypothesis: birthplace or nationality does not
measure training location, specialization age or annual workload. A
development-history variable would need its own source and would still face
severe selection and age confounding.

## Development-history proxies and sources

Added later on 2026-11-03 after the question was refined to whether players
carry more competition load than in the past, in games and across a career
from youth to the professional level, and whether that explains injuries.

### What exists, by how it can be collected

- **NBA player bios (stats.nba.com `CommonPlayerInfo`, probed through the
  project's `nba_api` path, 34 fields, 0.5 s):** birthdate, country, school
  and last affiliation, draft year/round/pick, first and last season. These
  identify affiliations and dates of NBA entry only. They give age at debut
  and draft path; they do not give years of high-school or college play,
  enrolment dates, transfers, redshirts or reclassification.
- **Draft combine (`DraftCombineStats`, probed: 81 rows for 2023-24):**
  height, weight, wingspan, body fat, standing and maximum vertical, lane
  agility, sprint. A physical-profile covariate from the early 2000s.
- **College player box scores (sportsdataverse-data release
  `espn_mens_college_basketball_player_boxscores`, ESPN-derived, MIT):** one
  row per player per game, 55 columns including `game_date`, `minutes`,
  `did_not_play`, `starter`, `season_type` and ESPN `athlete_id`. Asset sizes
  show usable coverage from ending season 2005 (2003 and 2004 are near-empty);
  2015 holds 182,630 rows over 5,930 games and 2024 holds 198,586 rows over
  6,241 games. About a third of rows are DNP entries with null minutes.
  Coverage by season must still be checked game by game before use. ESPN
  athlete IDs carry from college into the NBA only for recent careers: the
  2024 college IDs for Zach Edey and Reed Sheppard resolve on ESPN's NBA
  athlete endpoint, but college rows for careers before roughly 2016 carry
  legacy five-digit IDs (Kevin Pangos: college 56798, NBA 2583962), so 272 of
  the 317 US college players in the report-era pool whom the ID link missed
  were found again only by name. Any build needs a resolution ledger
  (crosswalk ID, then unique plausible name match, with ambiguous names left
  unresolved) and must record the method per player. Linking
  ESPN IDs to NBA `person_id` uses the hoopR-nba-data
  `nba/crosswalk/parquet/nba_player_crosswalk_<season>.parquet` files
  (seasons 2002–2027, 458–744 rows each, refreshed 2026-10-26 and
  2026-11-03): `espn_athlete_id` to `nba_player_id` with `match_method`,
  `match_confidence` and `match_keys`; the 2026 file matches 672 of 673 rows
  by exact name on game, team and jersey. Both the college release and the
  crosswalk are third-party derivatives and would be pinned by commit and
  SHA-256 as the hoopR schedules already are.
- **International and G League seasons:** FIBA player pages carry
  competition histories (for example Dončić's), RealGM and Eurobasket carry
  club seasons; terms unverified. G League minutes exist on NBA stats; not
  probed.
- **Grassroots circuits:** no official historical archive was found. Nike
  EYBL rosters and statistics exist on the league site and on DraftExpress
  team pages from about 2010; earlier participation is biography only. A
  missing circuit record never establishes non-participation.
- **Multisport participation, years of varsity play, prep years,
  reclassification:** no database. Two published studies built these by hand
  from public records for first-round picks: Rugg et al. 2018 (237 picks,
  2008–2015, 15 % multisport, fewer major injuries 25 % against 43 %) and Sang
  et al. 2025 (318 picks, 2013–2023, 27 % multisport, 13.5 % against 16.9 % of
  games missed). The prevalence figures describe different cohorts and
  samples and both report associations favouring multisport backgrounds; the
  difference is a reason to check definitions, not evidence that the
  classifications drift.
- **Team media guides** sometimes document development histories in detail
  (the 2023-24 Orlando guide records Suggs's football and youth national-team
  tournaments) and are a research source, not a complete workload record.
- **Sports Reference college pages** carry seasons, games and minutes, but
  the site rate-limits automated access to 20 requests a minute and its terms
  prohibit spidering and building a database from its data, so it is a manual
  reference here, not a bulk source.

### How a development ledger would work

A reviewed reference table in `data/reference/`, in the style of the venue
capacity ledger: dated participation records rather than one permanent label
per player (sport, level, program, seasons or ages, last documented
participation), each with the supporting passage, source link, dates and an
uncertainty note. Unknown stays distinct from confirmed absence: a biography
that mentions football establishes participation, one that mentions only
basketball does not establish basketball-only. The build binds the ledger's
hash into any analysis manifest.

### Design rules the reviews established

- Choose entry cohorts before looking at outcomes (draft class, roster at a
  season start), and account for differing follow-up; never select players by
  later minutes reached, because injury itself prevents reaching thresholds.
- Two separate comparisons: within-player for NBA workload against subsequent
  injury (a player's recent load changes), between-player for development
  background (a player's youth history never changes).
- The denominator follows the outcome: new in-game injuries per minutes at
  risk; absence burden per scheduled eligible player-games or team-games;
  never missed games divided by minutes played.
- Prior injury history, dated and sourced, is a required alternative
  explanation.
- Measures to prioritise: competition workload by age (college, professional
  and national-team games and minutes at comparable ages), dated development
  pathway stints, multisport participation, grassroots participation,
  off-season competition, and prior injury history.
- Start with a 30–50-player feasibility pilot sampled across predefined
  development pathways without reference to injury outcomes; measure research
  time, missingness and agreement between independent classifications; then
  choose the full cohort from the usable exposure variation and injury-event
  counts. For sizing only: 1,278 players appeared in 2018-19 through 2025-26
  and first-round picks over those drafts number about 240.
- Fix the research question and analysis rules before results; choose the
  public headline after.

### Pilot started 2026-11-03

The pilot sample, templates and protocol are in
`analysis/injury/development_pilot/` (40 players, 8 per pathway stratum,
seed 20261103, 12 flagged for a blind second pass). Resolution of the
1,278-player frame to ESPN college seasons: 779 by crosswalk ID, 274 by
unique plausible name, 21 ambiguous names, 204 unresolved (92 % of US players
link to at least one college season). The draw script and its cached inputs
are throwaway spike material.

Corrections recorded 2026-11-03 after a second review of the pilot:

- **Blindness deviation.** Specific findings for five of the twelve
  blind-subset players were disclosed in conversation before the user's pass
  (Martin's football and triple jump, Smith's football and baseball, Towns's
  baseball, Harrison's football, Green's spring-game cameo). Agreement on that
  subset may reflect shared leads. A second, untouched subset of twelve was
  drawn from the remaining players (seed 20261104) for a defensible
  independent-agreement estimate; it is not to be researched or discussed by
  the second rater until the first pass is complete.
- **Classification is a later step.** A single appearance in another sport
  (a spring-game cameo, a one-off meet) does not establish multisport
  development. Raw participation records are kept as recorded; a defined
  developmental classification (ages, duration, competitive level) is applied
  afterwards and documented.
- **Unresolved pathways are now represented.** Six players from the
  unresolved stratum (mostly college careers before the 2005 start of the
  ESPN data) were added to the sample so that research effort and
  missingness are not extrapolated from systematically classified players
  only.

## Star players as a central comparison

Raised on 2026-11-03: injuries may feel unusually damaging even when
league-wide absence counts are unexceptional if absence has become
concentrated among established stars, and if those stars' preceding workloads
have changed. Candidate headline question, left open: *has injury-related
absence become more concentrated among established stars, and have their
preceding workloads changed?*

Rules fixed before any result:

- **Star definition** follows the league's Player Participation Policy: a
  player selected to an All-Star team or an All-NBA team in any of the
  previous three seasons, frozen before each season's opening night. The
  narrower "previous season only" definition is a sensitivity check, because
  it drops a star whose injury prevented selection, the very thing under
  study. Top-50 by previous-season minutes is a separate workload grouping
  (opportunity and durability), not a quality tier. Rookies and players
  without a usable prior record form their own explicit category.
- **Outcomes stay separate and keep their timing.** Unavailability: injury
  listings divided by rostered game opportunities (from full box scores).
  New injuries: identified episodes against exposure where they occurred.
  Consequence: games and calendar days lost per episode, with ongoing
  episodes treated as censored. Workload before outcome: minutes, usage and
  tracking measured in windows preceding the event, with age, prior injury
  and season considered; season-total minutes are not a causal predictor
  when the injury itself reduced them.
- **Workload measures stay separate.** Minutes (player-game table,
  2012–2026), box-score usage rate (field-goal attempts, free-throw attempts
  and turnovers against team totals, computable from the merged tables for
  2012–2026), and tracking distance and speed (2013-14 onward) describe
  different things; usage captures possession-ending offensive involvement
  only, not defending, screening or contact. No combined "intensity" score.
- **Coverage differs by measure.** Workload runs from 2012 but injury
  listings only from 2018-19, so any look at the two together is bounded by
  the shorter series.
- **Confounders:** age (stars are older), labelling (management and rest
  listings concentrate on stars; the 65-game rule changed incentives from
  2023-24), and reporting incentives.

Relevant evidence: Tummala et al., *Characterization of Ankle Injuries and
Associated Risk Factors in the NBA: Minutes Per Game and Usage Rate
Associated With Time Loss* (Orthop J Sports Med, July 2023; 2015-16 through
2020-21, 554 ankle injuries, 4.06 per 1,000 game exposures). Verified from
the abstract: occurrence was associated with games played and prior
hip/hamstring/quadriceps injury, while greater minutes per game and usage
rate were associated with longer absences. It supports separating frequency
from time lost and says nothing about a trend among stars.

## What the data can and cannot test

| Hypothesis from the discussion | Measurable variable | Source and coverage | Status |
|---|---|---|---|
| Injury burden is rising | Injury `Out` player-games per scheduled team-game; games missed by rotation players | Official injury reports 2018-12-17→ (PDF primary; sportsdataverse derivative); D1 appearance gaps 2012–2026 as a pre-2018 proxy | Reachable today; needs D2 design and an in-progress 2027 feed |
| New injuries versus ongoing recovery | Episode onset from status transitions; games missed per episode | Injury reports plus D1 appearances | Reachable today; needs episode-definition rules |
| More possessions per game | Possessions ≈ FGA − OREB + TOV + 0.44·FTA per team-game; exact counts from play-by-play | `team_games.parquet` 1984–2026; `pbp` 1997–2026 | In hand now |
| More distance and speed per game | Player and team miles per game, average speed | stats.nba.com `LeagueDashPtStats` (SpeedDistance), 2013-14→ | Season summaries confirmed; game-level completeness unqualified |
| Injury type mix | Anatomy and reported diagnosis parsed from the listed reason; mechanism needs separate evidence and an "unknown" category | Injury reports 2018-12-17→ (format drift noted above) | Reachable today |
| Workload and schedule compression | Minutes in trailing windows, back-to-backs, games in last 7 days, travel | D1 `player_games` 2012–2026 plus D3 schedule features (not designed) | D1 merged; D3 needs design |
| Competition load by age across a career | Cumulative college, professional and national-team games and minutes at comparable ages | ESPN college box scores 2005→ (ID crosswalk needed); D1 NBA minutes trusted from 2012 only, so "observed since 2012" or backfill; FIBA histories | Partly reachable; needs the crosswalk and a backfill decision |
| Development history (multisport, specialization age, training location) | Dated pathway stints, multisport and grassroots participation from a reviewed ledger | Hand research from public records (media guides, FIBA pages, biographies), piloted first; nationality is a poor proxy | Between-player comparison; pilot before committing |
| Prior injury history | Dated, sourced injuries before the observation period | Injury reports 2018-12-17→; manual before that | Reachable for the report era |
| 65-game rule changes behaviour | Management/rest listings before and after 2023-24; games played by award candidates | Injury reports; D1 appearances | Reachable today |
| Age, position and prior injury controls | Age on game date, position, prior episodes | Player biographies; injury-report history | Biographies not probed |

## Implications

1. **Current-season games need a new contract, not a range bump.** The
   modern pipeline's exact-count gates cannot pass on a season in progress.
   The defensible path is a separate, explicitly mutable, dated and
   refreshable in-progress publication (final games only, snapshot-dated
   manifest, hoopR cross-validation, promoted into the completed-season
   contract after the 2027 playoffs), designed before any code, while the
   completed-season validation rules stay untouched. Sources for it are
   confirmed reachable today. The injury question needs it: without 2027
   appearances and minutes, the current season cannot be compared on
   workload.
2. **D2's source question is substantially answered.** A primary source
   (official PDFs, public S3, attested bodies plus sidecars in the repository's
   usual style) and an MIT-licensed, daily-refreshed parsed derivative with
   `person_id` resolution both exist back to 2018-12-17. The derivative can
   bootstrap and cross-validate a repository parser; it should not be the
   only evidence. Pro Sports Transactions stays out.
3. **D2's denominator needs full box scores, not the injury report.** The
   report lists only players a team declares unavailable or limited, and the
   player-game table lists only players credited with an appearance. Checked
   on 2026-11-03 for Houston at Oklahoma City, 2025-10-21 (game
   `0022500001`): the player-game table holds 20 players, the report lists 9,
   and the league box score (`BoxScoreTraditionalV3`, one request, 0.2 s)
   holds 27 active players, including five "DNP - Coach's Decision" entries
   (Jeff Green, Aaron Holiday, Ousmane Dieng, JD Davison, Kevon Harris) that
   appear in neither of our sources. Healthy bench players are therefore
   invisible to "appeared or listed", which would bias exactly the
   stars-versus-role-players comparison. Full box scores recover the dressed
   roster for every game and are a practical acquisition (about 1,230
   requests a season); inactive lists and transactions are still needed for
   players not dressed. Pre-2018 seasons have no official report at all.
4. **The analysis needs its own design, structured as three separate
   measurements** (as the reassessment recommends): absence burden against
   complete schedules with explicit reporting-coverage checks; new-injury
   incidence and severity, distinguishing new episodes from ongoing
   recovery and following games missed over time; and candidate
   contributors (preceding workload, rest, schedule density, age, prior
   injury), with tracking and development history added only where coverage
   supports them. The probe shows the question is answerable with current
   sources and that counts alone will not settle it. The design must keep
   the within-player workload comparison and the between-player development
   comparison separate, fix cohorts and denominators in advance, and begin
   the development ledger with the 30–50-player pilot.

## Follow-up

- Merged-`master` full test suite: 1,528 passed (see Test status above).
- Worktree `.worktrees/player-games-workload` removed and branch
  `feat/player-games-workload` deleted after the merged suite passed. The
  worktree-only SDD workspaces for both player-games plans were preserved
  under the main checkout's gitignored `.superpowers/sdd/`.
