# Slice 2 source/schema spike — findings

Date: 2026-07-29. Run from riz-llm (egress IP 68.204.108.251) using the
repository `.venv` (Python 3.12, requests 2.34.2, nba_api 1.11.4, pandas 3.0.5).
Cached artifacts: `data/raw/spike/` (gitignored; bref HTML + meta JSON, hoopR
parquet samples).

## Provider 1: stats.nba.com (nba_api) — WORKS after AdGuard correction

The initial TLS diagnosis was wrong. The configured DNS resolvers
(`192.168.10.1` and `10.200.0.1`) returned `0.0.0.0` for `stats.nba.com`.
`curl` consequently connected to a local port-443 listener, which returned
`TLSV1_ALERT_INTERNAL_ERROR`. Public DNS resolved the hostname through Akamai,
and a direct-IP diagnostic completed TLS with the valid `*.nba.com`
certificate. The user then found the blocked requests in AdGuard and
allowlisted the hostname.

After the AdGuard correction, ordinary system DNS returned a real Akamai
address and the installed `nba_api` 1.11.4 completed `LeagueGameFinder`
requests without proxying, TLS impersonation, or disabled certificate
verification:

| Ending season | team rows | unique games | rows/game | GAME_ID prefix |
|---|---:|---:|---:|---|
| 2021 | 2,160 | 1,080 | exactly 2 | `002` |
| 2024 | 2,460 | 1,230 | exactly 2 | `002` |
| 2026 | 2,460 | 1,230 | exactly 2 | `002` |

All three responses used the same 28-column schema as the archived historical
data:
`SEASON_ID, TEAM_ID, TEAM_ABBREVIATION, TEAM_NAME, GAME_ID, GAME_DATE,
MATCHUP, WL, MIN, PTS, FGM, FGA, FG_PCT, FG3M, FG3A, FG3_PCT, FTM, FTA,
FT_PCT, OREB, DREB, REB, AST, STL, BLK, TOV, PF, PLUS_MINUS`.

The 2023-24 and 2025-26 first-party regular-season results already exclude the
NBA Cup championship, so no first-party Cup exception is required. The fetch
preflight must nevertheless reject a future `0.0.0.0`/loopback DNS answer
with an AdGuard-focused diagnostic.

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

## Provider 3 (schedule/enrichment): sportsdataverse/hoopR-nba-data — WORKS, rich

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

## Source decision

The earlier option (a) selection was based on the incorrect TLS diagnosis and
is superseded by the successful post-AdGuard checks. The implemented source
roles are:

- **stats.nba.com via nba_api:** canonical team-game box scores and NBA
  `GAME_ID`s for 2019-20 through 2025-26, cached one season at a time.
- **hoopR:** schedule IDs, neutral-site flags, venue fields, and ESPN
  attendance for enrichment/cross-validation; not the canonical box score.
- **basketball-reference:** attendance and arena source of record.

## Follow-up during Slice 2A plan review

Before the AdGuard cause was found, a second read-only audit pinned the
hoopR repository at commit
`50fe7e429c6b5bcbe62e7a56d19afe5a80e6e143` (2026-07-28) and inspected all
three parquet families needed for ending seasons 2020–2026: schedules,
team-box scores, and player-box scores.

After requiring `season_type == 2`, a completed status, and two resolvable NBA
franchises, the schedule counts are:

| Ending season | mapped schedule games | canonical regular-season games | reason for surplus |
|---|---:|---:|---|
| 2020 | 1,059 | 1,059 | none |
| 2021 | 1,080 | 1,080 | none |
| 2022 | 1,230 | 1,230 | none |
| 2023 | 1,230 | 1,230 | none |
| 2024 | 1,231 | 1,230 | NBA In-Season Tournament championship |
| 2025 | 1,231 | 1,230 | NBA Cup championship |
| 2026 | 1,231 | 1,230 | NBA Cup championship |

The championship rows have `type_abbreviation == "CC"` and do not count in
regular-season standings. They must be excluded by that explicit field, while
Cup group, quarterfinal, and semifinal games remain regular-season games.

The 2020-21 `team_box_2021.parquet` file contains only 1,036 regular-season
game pairs after exhibition filtering: 44 canonical games are absent, heavily
concentrated in late-season Chicago and New Orleans games. In contrast,
`player_box_2021.parquet` covers all 1,080 canonical games. This no longer
requires a production fallback because NBA Stats supplies the complete
canonical box scores. It remains useful evidence for keeping hoopR team-box
data out of the critical path. All other inspected seasons had team-box
coverage for every retained schedule game.

A future TLS exception from any working provider remains a fail-closed source
error. Disabling certificate verification, changing TLS fingerprints, or
silently substituting sources is not an approved remedy.
