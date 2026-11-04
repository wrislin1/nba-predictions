# Development-history pilot (started 2026-11-03)

A throwaway feasibility pilot, not a repository publication. It measures how
long hand research of player development histories takes, how much is
missing, and how well two independent researchers agree, on a small sample
drawn **without reference to injury outcomes**. Its result decides the full
cohort for the injury analysis. Context and the design rules it serves:
`docs/audits/2026-11-03-injury-source-and-current-season-spike.md`.

## The sample (`pilot_sample.csv`, n = 46)

- **Frame:** the 1,278 players with at least one regular-season appearance in
  `player_games` for ending seasons 2019–2026.
- **Strata** come only from systematic fields: NBA `PlayerIndex` country and
  college, and ESPN college seasons joined through the hoopR ESPN-to-NBA
  crosswalk with a unique, plausible display-name fallback (match method
  recorded per player). Counts in the frame: A US multi-year college 660;
  B US one college season 195; C US no college 10; D international without US
  college 147; E international with US college 146; X unresolved 120 (mostly
  college careers before ESPN coverage begins in 2005), not sampled.
- **Era:** NBA debut year 2019 or earlier ("early") against later ("late"),
  four and four per stratum where available. Seed 20261103, 8 per stratum.
- **Unresolved addendum:** 6 players from stratum X (seed 20261105): three
  with college careers before the ESPN data begins (Haslem, Devin Harris,
  Iguodala) and three whose pathway the systematic fields could not place
  (Ferguson via Adelaide, Barlow and Ausar Thompson via Overtime Elite).
  Without them, research effort and missingness would be extrapolated only
  from players the systematic fields could classify. Their presence also
  shows that the C stratum (US, no college) is undercounted by the heuristic:
  overseas and Overtime Elite routes carry club names in the college field.
- **Agreement subsets:** `blind_subset = True` (subset A, 12 players) was
  researched by Claude on 2026-11-03, before the first pass; see the
  deviation below. `blind_subset_b = True` (subset B, 12 players drawn from
  the remaining 28 with seed 20261104) is untouched: Claude researches it only
  after the first pass is complete and says nothing about those players until
  then. Research every player the same way regardless of flags.
- The ESPN college-season counts are provisional inputs to the strata, not
  findings; the research should confirm college seasons too.
- Rerun: `python draw_pilot_sample.py <scratch> <player_games dir>
  <college box dir> <crosswalk dir> <out dir>`; inputs are cached, untracked,
  with checksums under `data/raw/spike/development_pilot_2026-11-03/`.

## What to record (`participation_records.csv`)

One row per dated participation record, never one label per player.

| Column | Meaning |
|---|---|
| `record_type` | `high_school`, `prep_postgrad`, `reclassification`, `grassroots_circuit`, `other_sport`, `college`, `pre_nba_pro`, `g_league`, `national_team`, `offseason_competition`, `prior_injury`, `other` |
| `claim_kind` | `participation` for evidence that something happened; `explicit_absence` only when a source states non-participation ("played only basketball in high school"). No row means unknown. |
| `sport`, `level`, `organization`, `location` | what, at what level (varsity, club, U16, pro league), for whom, where |
| `start`, `end`, `seasons_or_ages` | ISO dates or season labels (`2001-02`); ages if that is all the source gives |
| `games_documented` | a count only when the source states one |
| `evidence_passage` | the sentence or two, verbatim, that supports the row (under 300 characters) |
| `source_url`, `source_type`, `source_date` | link; `media_guide`, `league_record`, `federation`, `school`, `news`, `encyclopedia`, `recruiting`, `forum`; publication date if shown |
| `confidence` | `confirmed` (official or primary: media guide, federation, school, league record), `reported` (reputable secondary, or an encyclopedia entry with a citation), `weak` (forum, unsourced) |
| `researcher` | your handle, or `claude` for the blind pass |

Rules: every row cites a source; nothing is filled from memory. Unknown stays
unknown. A biography that mentions football establishes participation; one
that mentions only basketball does not establish basketball-only. For
`prior_injury`, record only injuries dated before the 2018-19 season; do not
look up injuries from 2018-19 onward for any sampled player.

## Procedure per player (`research_log.csv`)

1. Note the start time. Cap is 20 minutes; stop at the cap and log what is
   still missing.
2. Source order: current or most recent team media guide (PDF; search the
   name); NBA.com and Basketball-Reference biography (high school, college,
   draft); Wikipedia, following its citations and citing the underlying source
   where possible; RealGM profile (pre-draft and overseas history); FIBA
   player page (national teams, youth tournaments); MaxPreps, 247Sports,
   Rivals or ESPN recruiting (high-school seasons, grassroots program); one
   targeted news search for other sports and for an AAU or circuit program.
3. Write one row per record found. Log minutes, sources checked, rows
   written and whether the cap stopped you.

## What the pilot reports

Research time per player by stratum; missingness per record type by stratum;
on the blind twelve, agreement per record type (both found, one found,
neither) and on the derived classifications (multisport: yes or unknown;
college seasons; grassroots program named). From those, a full-cohort choice
with its expected research cost and exposure variation.

The example rows in `participation_records.csv` are illustrative and are not
in the sample; delete them before any analysis.

## Corrections and deviations (2026-11-03)

- **Blindness deviation on subset A.** After completing its pass, Claude
  summarised findings for five subset-A players in conversation (Martin,
  Smith, Towns, Harrison, Green) before the first pass had started. Agreement
  on subset A may therefore reflect shared leads; subset B is the defensible
  independent-agreement estimate.
- **Classification comes after recording.** A single appearance in another
  sport (a spring-game cameo, one meet) does not establish multisport
  development. Record participation as found, with ages, duration and level;
  a written developmental classification is applied afterwards.
- **Source access observed in the first automated pass:** RealGM and SLAM
  refuse automated fetchers (403); old Rivals links redirect to a dead root;
  FIBA player pages and school roster bios (FAU, Florida) render only with
  JavaScript; the Wikipedia API rate-limits bursts. A person in a browser
  will reach some of these; log what you could not open.
