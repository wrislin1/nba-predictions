# Slice 2 source/schema spike — findings

Date: 2026-07-29. Run from riz-llm (egress IP 68.204.108.251) using the
repository `.venv` (Python 3.12, requests 2.34.2, nba_api 1.11.4, pandas 3.0.5).
Cached artifacts: `data/raw/spike/` (gitignored; bref HTML + meta JSON, hoopR
parquet samples).

## Provider 1: stats.nba.com (nba_api) — BLOCKED at TLS

- `LeagueGameFinder` (2020-21 regular season) failed with
  `SSL: TLSV1_ALERT_INTERNAL_ERROR` — the server aborts the TLS handshake at
  Client Hello, before any HTTP exchange.
- Reproduced with `curl` (OpenSSL): identical alert. Headers are irrelevant at
  this layer; this is TLS-fingerprint (JA3-style) bot filtering at the Akamai
  edge, a widely reported condition for non-browser clients.
- Control: `cdn.nba.com` static JSON returns HTTP 403 "Access Denied" even
  with full browser headers.
- Per the design's no-bypass constraint, no impersonation or evasion was
  attempted. Options that respect the constraint are listed at the end.

## Provider 2: basketball-reference month pages — WORKS

Seven pages fetched with plain `requests` + browser User-Agent at 12 s
spacing; all HTTP 200; HTML + request metadata cached.

**Attendance semantics (the blank-vs-zero question) — answered:**

| Page (2020-21) | rows | blank/NaN | zero | positive |
|---|---|---|---|---|
| Dec 2020 | 67 | 0 | 56 | 11 |
| Jan 2021 | 222 | 0 | 181 | 41 |
| Feb 2021 | 212 | 4 | 131 | 77 |
| Mar 2021 | 204 | 1 | 101 | 102 |
| Apr 2021 | 240 | 0 | 77 | 163 |
| May 2021 | 173 | 2 | 27 | 144 |
| Jan 2024 (control) | 231 | 0 | 0 | 231 |

- b-ref records **explicit `0`** for fan-free games; blanks are rare (7 in
  2020-21, 0 in the normal-season control) and represent genuinely missing
  reports. The spec's `attendance_status` mapping is therefore directly
  observable: `0` → `reported_zero`, blank → `missing`.
- Positive COVID-season values start as low as 204–750 (limited capacity),
  confirming the dose dimension exists in-source.
- Month-page schema: `Date, Start (ET), Visitor/Neutral, PTS, Home/Neutral,
  PTS.1, [boxscore link], [OT], Attend., LOG, Arena, Notes`. The `Arena`
  column supports the `venues` table directly.

## Provider 3 (fallback probe): sportsdataverse/hoopR-nba-data — WORKS, rich

GitHub-hosted pre-scraped ESPN data, no scraping required. Verified contents:

- Directories: `team_box`, `player_box`, `schedules`, `pbp`, `officials`,
  `betting_lines`, `standings`, … with parquet per season **through 2025-26**.
- `team_box_2021.parquet` (2,242 rows × 57 cols): includes `fouls`,
  `technical_fouls`, `flagrant_fouls`, `free_throws_attempted/made`, full
  shooting/rebound/turnover stats — sufficient for Elo and the referee
  channel.
- `nba_schedule_2021.parquet` (1,204 rows × 75 cols): includes `attendance`,
  **`neutral_site`**, `venue_id`/`venue_full_name`/city/state,
  `season_type` (2 = regular, 3 = playoffs, 5 = play-in),
  `status_type_completed`.
- **Cross-validation vs b-ref (Dec 2020 completed games): exact agreement** —
  67 games, 56 zero-attendance, 11 positive on both sources.
- Reconciliation item for `build.py`: hoopR shows 1,081 completed 2020-21
  regular-season games vs the canonical 1,080 (off-by-one; likely a
  suspended/rescheduled duplicate or misclassified row).
- Caveat: ESPN identifiers (`game_id`, `team_id`) differ from the legacy
  stats.nba.com IDs in the 1983–2019 data; joining requires a date+team
  mapping layer (the spec's provider-adapter). The repo's `crosswalk`
  directory may help.

## Box-score source decision (user call required)

The legacy 1983–2019 data is stats.nba.com schema; the seven missing seasons
need a box-score source. Options, none of which violate the no-bypass rule
except (c):

- **(a) hoopR as box-score + schedule provider** for 2019-20 → 2025-26, with
  b-ref month pages as the attendance/arena source of record and hoopR
  attendance as cross-validation. No scraping beyond b-ref, proven schemas,
  includes neutral-site flags and officials data. Requires the mapping layer.
- **(b) Browser-assisted nba_api fetch** — the block is TLS-fingerprint only;
  a real browser session can retrieve the ~7 season-level LeagueGameFinder
  responses (one per season), preserving stats.nba.com schema continuity.
  Manual/assisted step, small volume, repeatable each season.
- **(c) TLS impersonation (curl_cffi)** — technically effective, but it is
  precisely the access-control bypass the design forbids; would require the
  user to explicitly amend the spec.
