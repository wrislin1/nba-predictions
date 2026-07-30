# Playoff Game Results (1947–2026) — Design

Date: 2026-07-30
Status: Approved (brainstorming session)

## Purpose

Capture every official post-regular-season BAA/NBA game — playoff series
games, play-in tournament games, regular-season tiebreakers, and the one
voided protest game — into a new parquet table, with the same archival
rigor as the historical regular-season collection: raw HTML preserved
locally and on the NAS, deterministic rebuilds, curated per-season count
validation, and snapshot/restore support.

The driving goal is data preservation: acquire the data now, while the
external sources are available, rather than depending on them later.

## Current state (verified 2026-07-30)

- `data/parquet/games.parquet` (49,117 rows, 1984–2026) and
  `data/parquet/historical_games.parquet` (19,405 rows, 1947–1983) are
  regular-season only by design.
- 1947–1983 playoff games are **already archived**: the raw pages in
  `data/raw/historical_bref/` contain them; `historical_build` identifies
  1,237 postseason rows via the "Playoffs" separator and drops them.
- 1984–2019 playoff games are **not in hand**: the legacy source
  (`data/raw/legacy/nba-teams-1984-2019-ordered.csv`) is regular-season
  only at source (every `SEASON_ID`/`GAME_ID` carries the regular-season
  `2` prefix), and `data/raw/bref/` has no pages before 2020.
- 2020–2026 playoff games are **already archived** in the raw hoopR
  schedule parquets (`season_type` 3 = playoffs, 5 = play-in); the loader
  filters them out (`hoopr.py`, `season_type == 2`). However,
  `data/raw/bref/` holds only regular-season months (~7 pages/season), so
  the BREF playoff-month pages for 2020–2026 are not yet fetched.
- The excluded edge games (tiebreakers in 1948/1950/1957; one
  protest-voided game) currently appear in **no** output table.

## Decisions

1. **Placement: separate table.** Playoff games go to a new
   `data/parquet/playoff_games.parquet`. `games.parquet` and
   `historical_games.parquet`, their consumers, and their validation
   machinery remain byte-identical and untouched.
2. **Source: Basketball-Reference monthly schedule pages for all eras.**
   One uniform parser and one durable raw-HTML archive covering
   1947–2026. hoopR serves as an independent cross-check for 2020–2026;
   stats.nba.com is not used.
3. **Scope: everything post-regular-season, labeled.** A `game_type`
   column distinguishes `playoff` / `play_in` / `tiebreaker` / `voided`,
   so every official game that was played lands in exactly one table.
4. **One table spanning both eras.** The regular-season era split exists
   only because its sources differ; playoff data has a single source and
   parser, so a single table is simpler and natural.

## Acquisition

New fetching (one-time, rate-limited like the historical fetch):

- All months for seasons 1984–2019 (~320 pages + meta sidecars).
- The missing playoff months (roughly April–June; season-dependent,
  including the 2020 bubble months) for 2020–2026.

1947–1983 pages are already on disk and in NAS snapshots; they are not
re-fetched.

**Open item (resolve in the plan):** whether 1984–2019 pages land in
`data/raw/bref/` or a new directory. Either way, the acceptance test is
that a full `games.parquet` rebuild remains byte-identical with the new
files present, because the modern attendance matcher reads
`data/raw/bref/`.

## Output schema

`data/parquet/playoff_games.parquet`, one row per game, mirroring the
`historical_games.parquet` schema:

- `game_id` (bref boxscore-slug IDs with deterministic fallback),
  `season`, `league`, `game_date`
- `away_team_name`, `home_team_name`, `away_franchise_id`,
  `home_franchise_id`
- `away_pts`, `home_pts`, `home_margin`, `overtime_periods`
- `site_status`, `venue_name`, `attendance`, `attendance_status`
- `boxscore_url`, `schedule_url`
- **new:** `game_type` ∈ {`playoff`, `play_in`, `tiebreaker`, `voided`}

A pinned manifest (`playoff_games_manifest.json`) accompanies the table,
following the historical pattern.

## Classification

- **Playoff:** rows after the in-table "Playoffs" separator (existing
  separator logic, including the curated 1947/1980 boundaries and the
  verified duplicate-separator regions), minus any rows classified below.
- **Play-in (2021+):** identified on the BREF pages and verified
  game-by-game against the hoopR raw parquets (`season_type == 5`), which
  enumerate the exact play-in games for every play-in season. Where BREF
  places play-in rows relative to the separator must be verified during
  implementation, not assumed.
- **Tiebreaker:** rows with the `tiebreaker` remark (1948, 1950, 1957),
  currently excluded from all outputs.
- **Voided:** the single "protest; does not count toward record" game.

## Validation

Same rigor as the historical work:

1. **Curated per-season expected counts** in a reference CSV with a
   provenance note per season (playoff series results are documented
   history; per-season game totals are externally derivable).
2. **Full hoopR cross-check for 2020–2026:** dates, teams, and scores of
   every playoff and play-in row must match `season_type` 3/5 rows in the
   raw hoopR parquets.
3. **Determinism:** rebuild reproduces the parquet and manifest
   byte-identically.
4. **Integrity checks:** no duplicate game IDs, no null scores, no ties,
   `home_margin` consistent, dates within each season's postseason
   window, zero overlap with either regular-season table's game IDs.
5. **Non-interference:** `games.parquet` and `historical_games.parquet`
   rebuilds remain byte-identical after all changes.

## Archival

Extend the NAS snapshot pattern
(`/mnt/nas/llm/documents/nba-predictions-archive/`): snapshot the new raw
pages with `SHA256SUMS` and the pinned playoff manifest, named by
manifest-hash prefix; extend `scripts/restore_historical_raw.sh` (or add a
sibling script) to cover the new snapshot family.

## Non-goals

- No round/series metadata (not on schedule pages; derivable later from
  dates and matchups if ever needed).
- No ABA games.
- No box scores — game results only.
- No changes to modeling inputs, `games.parquet`,
  `historical_games.parquet`, or the regular-season pipeline.

## Implementation shape

One project, one implementation plan; the parser, classifier, and
validation are shared across eras, so splitting by era would duplicate
machinery. Task order: fetch new raw pages → parser/classification +
build command → validation + tests → NAS snapshot.
