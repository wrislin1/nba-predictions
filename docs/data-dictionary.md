# Game-data dictionary

This is the contract for the committed Slice 2A publication. It covers
regular-season games from ending season 1984 (1983-84) through ending season
2026 (2025-26): 49,117 `games` rows and 98,234 `team_games` rows. A season
number is always its ending year. The exact publication, row counts, table
hashes, source cache paths, source SHA-256 values, validation results, and
dependency versions are recorded in the committed
[build manifest](../data/parquet/build_manifest.json); it is the authority if
this page and an artifact ever differ.

Related decisions are in the [revival design](superpowers/specs/2026-07-29-nba-repo-revival-covid-hca-design.md), the [Slice 2A plan](superpowers/plans/2026-07-29-slice2a-game-data-pipeline.md), and the corrected [source audit](audits/2026-07-29-slice2-source-spike.md).

## Tables and physical types

Types below are the actual Arrow/Parquet physical types (`large_string`,
`int64`, `bool`, and `date32[day]`), not inferred application types. Nullable
means missing values are represented as null; a nullable integer must never be
read as a zero-filled value.

### `team_games.parquet`

One canonical NBA box-score row per team per game; `game_id` has exactly two
rows, one `is_home=true` row and one `false` row.

| Column | Type | Meaning |
|---|---|---|
| game_id | large_string | Canonical NBA Stats `GAME_ID` |
| season | int64 | Ending season |
| source | large_string | `nba_stats` |
| franchise_id, opponent_franchise_id | large_string | Stable franchise codes |
| is_home | bool | NBA/schedule home designation |
| game_date | date32[day] | Game date |
| wl | int64 | Win (1) or loss (0) |
| pts, fga, fgm, fg3a, fg3m, fta, ftm, oreb, dreb, reb, ast, stl, blk, tov, pf | int64 nullable | Team box-score statistics |

### `games.parquet`

One paired game row, with final attendance, venue, and capacity enrichment.

| Column | Type | Meaning |
|---|---|---|
| game_id | large_string | Canonical NBA Stats `GAME_ID` |
| espn_game_id | large_string nullable | hoopR/ESPN ID prefixed `espn-`; absent for legacy |
| season | int64 | Ending season |
| source | large_string | `nba_stats` |
| game_date | date32[day] | Canonical game date |
| home_franchise_id, away_franchise_id | large_string | Stable franchise codes |
| home_pts, away_pts | int64 | Final points |
| neutral_site | bool | Schedule/corrected neutral designation |
| neutral_site_source | large_string | `hoopr`, a correction marker, or legacy assumption |
| venue_id, venue_name, venue_city | large_string nullable | Enriched venue identity/name/city |
| venue_source | large_string | `bref`, `espn`, or `legacy_unknown` |
| attendance_bref, attendance_espn | int64 nullable | Unselected provider-specific values |
| attendance | int64 nullable | Selected attendance under the precedence rule |
| bref_row_matched | bool | Whether a basketball-reference row matched |
| attendance_status | large_string | `reported_zero`, `reported_positive`, or `missing` |
| attendance_source | large_string | `bref`, `espn`, or `legacy` |
| attendance_coverage_exemption_reason | large_string nullable | Audited no-fan bubble exemption only |
| capacity | int64 nullable | Season-aware venue capacity |
| capacity_source_url, capacity_note | large_string nullable | Capacity provenance and exception note |

### `franchises.parquet`

| Column | Type | Meaning |
|---|---|---|
| alias | large_string | Source-era team name |
| franchise_id | large_string | Stable franchise code |
| first_season, last_season | int64 nullable | Inclusive ending-season validity bounds |

The mapping preserves organizational lineage rather than rewriting history:
Charlotte Hornets through 2002 map to `NOP`; Charlotte Bobcats and later
Hornets map to `CHA`; New Jersey/Brooklyn is `BKN`; Vancouver/Memphis is `MEM`;
Seattle/Oklahoma City is `OKC`; Kansas City/Sacramento is `SAC`; and
Washington Bullets/Wizards is `WAS`.

### `venues.parquet`

| Column | Type | Meaning |
|---|---|---|
| venue_id, franchise_id | large_string | ESPN venue and home-franchise IDs |
| first_season, last_season, capacity | int64 | Inclusive venue era and stated capacity |
| capacity_source_url, capacity_note | large_string | Auditable capacity source and context |

It contains 40 curated venue eras for ending seasons 2020–2022. A non-neutral
game in that primary window must resolve exactly one provenance-backed row;
neutral/bubble sites may have no capacity. Capacity is not inferred from
attendance. Attendance more than 105% of capacity requires an explicit
`ATTENDANCE_EXCEPTION:` note.

## IDs, enrichment, and missingness

NBA Stats supplies canonical modern box scores and `GAME_ID`; legacy rows keep
their canonical NBA IDs. `source` identifies the statistical provider, so it
is `nba_stats` for every published row. It does not identify the ingestion
path: ending seasons 1984–2019 come through restored archived NBA Stats CSVs,
while ending seasons 2020–2026 come through cached `LeagueGameFinder` JSON.
hoopR is schedule enrichment only: its ESPN game and venue IDs are distinct,
prefixed `espn-`, and join through ending season, date, home franchise, and
away franchise (with audited next-day reconciliation).
Every retained modern NBA game joins one hoopR schedule row. hoopR supplies
`neutral_site`, venue ID/city, and ESPN attendance; basketball-reference is
the attendance and arena source of record. Its arena wins when a matching,
nonblank arena exists, otherwise ESPN supplies the venue name.

For a matched basketball-reference row, `attendance` is its value, including a
blank/null. ESPN is used only when no b-ref row matches. Thus a b-ref blank is
`missing`, never an ESPN fallback or zero; an explicit source zero is
`reported_zero`, never missing. Positive values are `reported_positive`.
Legacy attendance retains its historical selected value and source. For each
modern season, the build enforces at least 95% b-ref row-join coverage. It
also enforces at least 95% known attendance after removing only the 87
explicitly enumerated 2020 no-fan bubble games whose matched b-ref attendance
is blank. The manifest separately records raw numeric coverage, the eligible
denominator, and the exemption count. Consequently, 2020 raw numeric coverage
is 91.7847%, while its exception-aware validation coverage is 100%.

The build rejects a game unless it has exactly two team rows, one winner, and
`wl` agrees with the final score. Every published box-score statistic is
non-null and nonnegative. If a legacy source is blank or contradictory, the
build requires an exact provenance-backed correction; it does not publish the
blank and never converts it to zero merely to satisfy a gate. Makes may not
exceed attempts, `oreb + dreb` must equal `reb`, and
`2 * fgm + fg3m + ftm` must equal `pts`. Corrections never convert a blank
legacy statistic to zero merely to satisfy an identity. A corrected field is
populated only when a historical box score supplies an exact value.

`reference/game_corrections.csv` has a deliberately narrow scalar grammar.
Team box values are canonical unsigned base-10 integers (`0`, or a nonzero
digit followed by digits): signs, decimal points, leading zeroes, and
whitespace are invalid. An attendance correction must be exactly blank, and a
neutral-site correction must be exactly lowercase `true` or `false`. Every
team-field correction carries its direct historical box-score URL and notes
the canonical NBA game page used to identify the game.

## Sources, pins, and hashes

All raw responses are gitignored under `data/raw/`. Every NBA Stats, hoopR,
and b-ref schedule body must have exactly one metadata sidecar containing the
matching SHA-256; orphan bodies, orphan sidecars, and metadata without a
matching SHA are rejected before publication. The manifest records only these
verified relative cache paths and hashes. The source roles are NBA Stats
`LeagueGameFinder` (canonical modern box scores), pinned hoopR (schedule,
neutral/venue/ESPN enrichment), basketball-reference (attendance/arena and
secondary exact historical reconciliation), and the two restored legacy
inputs. hoopR is pinned to repository
`sportsdataverse/hoopR-nba-data` commit
`50fe7e429c6b5bcbe62e7a56d19afe5a80e6e143`, never a mutable branch.
Each cached hoopR body must also have checksum-valid sidecar metadata with
exact provider `hoopr`, its exact integer ending season, and that exact
upstream commit. The manifest records the verified binding on every season
entry rather than relying only on the global pin. Before a full build, the
checkout copies of `franchises.csv` and `hoopr_snapshot.json` must match the
runtime package assets byte for byte, so normalization, publication, and
manifest attestation cannot use divergent reference data.

| Source | Ending season(s) / path | SHA-256 |
|---|---|---|
| NBA Stats | 2020 `leaguegamefinder_2020.json` | `9349ad765225a5586d37197285fd73a0f407e7f630bcd04884e445536b90ce6a` |
| NBA Stats | 2021 `leaguegamefinder_2021.json` | `379fb6bb4874fb21efc80f7ed782c5cfb68d8d078d68930d01e51e6e47a4de8a` |
| NBA Stats | 2022 `leaguegamefinder_2022.json` | `892c54e9c203ea1f4ba4b2ecda5215e7347d96bee34050bc2558c0fbe665a3e2` |
| NBA Stats | 2023 `leaguegamefinder_2023.json` | `42d18653724ef95d1a34a56426abf4069650ba607d5673230a828bea3a850e5b` |
| NBA Stats | 2024 `leaguegamefinder_2024.json` | `abee3c8041c58df0f1c3920835a1313109d87c7782b221710d348a2ce05ab91a` |
| NBA Stats | 2025 `leaguegamefinder_2025.json` | `f710bbe024bb093ffcee08593234db2ebafa63c80596b99a4e2cacff5b32eb67` |
| NBA Stats | 2026 `leaguegamefinder_2026.json` | `b5f223d082300c92187223577bfd1d3b78b13f3afd9af9eb9cd51e5ea5265601` |
| hoopR | 2020 `nba_schedule_2020.parquet` | `cfe7e328c34cf36f160be9b8adebf16806c5c932274c0177566deb2ac2d743bf` |
| hoopR | 2021 `nba_schedule_2021.parquet` | `7fbdfe4350da317a063dedf17ecdad0b1c0a6ebf40c605cf0131fb1b002b43c2` |
| hoopR | 2022 `nba_schedule_2022.parquet` | `22703c94919428bf8134533ee5f652b1d156980736ee41f14fd297fcd7bcb16d` |
| hoopR | 2023 `nba_schedule_2023.parquet` | `eddbd2de64d401cc9e44b1a567f52e408f71b96bc413a9dfd22cc4ad0e404f13` |
| hoopR | 2024 `nba_schedule_2024.parquet` | `7840d80ebb7ab34cedec77fb620519b88c282c2662b2a0f9f45d538457bbb37f` |
| hoopR | 2025 `nba_schedule_2025.parquet` | `157dd9c130dd7518812302e8ed50496e7c3773fbc9c7efc98c40f5ff39314355` |
| hoopR | 2026 `nba_schedule_2026.parquet` | `4033bfb809a0cf60e706e733cb8b1fb561dec185055957570a8583e22258f8be` |
| legacy | `league_boxscores_extra.csv` | `e512c1a15b5a2046c47efb86152b1087c28c8955b183fdf5c0e8151876f8b822` |
| legacy | `nba-teams-1984-2019-ordered.csv` | `3a71f75829452032523098280542485f2a94513facde2ea3f78b5b4c4356764c` |

The 49 basketball-reference paths are
`raw/bref/NBA_<ending-season>_games-<month>.html`; their exact month/hash
ledger is:

```text
2020 august:b56ad42ce7ae6e4e8e68639fe6b4f443a8ba139399c619fa29ddeb5f79a4bed1 december:5e1c28a5b2c78a16fca1f9168cd93f07adcc970eee8363856babaea44b6149dd february:1779faefc05f60cfd2bfc274189f5449114a87375be56673fb5c275e2c811b54 january:b1fae333a0eb3470d75e64409dc925e2edb0fffc9a300efc0559669bf4fbe138 july:75eea33530e890f4c793b5e9c45de39c1fec80926e6420197804e29782489fa6 march:cd2cb36ccd5cac57164c4e0ab28c39f9e6a0e34057b2a6056d46b495ee0eef5b november:147e9aff2b9fe48143351c0f01f4fad168c05368a0698454f52257a051940146 october:64372e11233b62c6589c2be38275153c35f49e9ccae365532ad8970f7f253d0c
2021 april:82a6032917dfed015220d70f08b12f22c95a99b442893ad6dba3fe70416c9139 december:dec166dd15c54513055888d945c2908320cd0961b44fe302bbf08a840d59d056 february:b8a16de349dff464d5264523e0567f96c905dd66b759a6dd63fc6a6da7511c34 january:c891625f18e83ed333b727cbcace42a44974aa3d31b55631b0dcb2c7a0a61388 march:5d286e83b260db15c5dbf193b04743eefff6970841d3ed4484343b66d3374d49 may:9350206440fc0ec9a533c5504f8f4c79d895f281d6b98b8b51a68a9b0b4cad8c
2022 april:79bbe7936089f25c0a554473a22c3398b50b76ec42179cb2d1555068701813d1 december:fc9313ef9ddc8b18ca72f3f6028358315367d7a6ebd888e9e397c26c35d8bb25 february:f6e838c71935e377f0191c3e93805681c923a5b0547bfca92be291045dbbdd02 january:94e44e79fa0ae0fbd08d1fb7794e82e00c00dd71e8d1061634ecbdb289af9ed5 march:cf699d1e5de4cf26634d82c2f2e3282a343804ad0c52c66974938a880450ba0a november:e0a92d4bc470562cc3b8e6fe16bc3d43913eabf43600aa6f76aebfbd40956bf0 october:5d0b6f8c2e5dce76540195ee3a5b4a6165db82eb3b0708d1edf03297e598e5c6
2023 april:191849498aeffb9cc3d2984a69f7715514a7b32b7c7eba9b220b192c9ad716c5 december:f4bc113308fcdeaff3c4a020fc005131d80eb697386d91f4698f8d12e1ae9005 february:7899cc10e2dda31e26530e854118ec9e0f04521d059e49217e4157639cbc9842 january:c2319537fd947777769076f655e0a2ed82a0601f462b69f3b49574e6cf141980 march:a72b88ebda50f0f99f4fd323b614b1b5745dad2af2854f3b16984be5d5fe9c09 november:709f1fd0cea0c29900ab89572907ffe8464d38667e2920dc7284aa719135a5e2 october:6256173ec1d86d03bdf9c5fe72699429f2037b306f0ad526c0eab2f015200197
2024 april:7f5f43aad61c58ff3855c1b29fa853b373168568a006c35c808d0e938e1a28bc december:ae38940a30162bad80266d771a7c2a71f20b140542c77530b1c6bed1494e0c69 february:7cabe1392176b3bd166465de4b63af677d3f71d007cfcbd4c6bf9ae679072821 january:cd92dee673321e1cbf37d3f4d34a9e38a9d8b8a8bb1e2e295e2d95a583ecdc3d march:27af72875b95d5f4a7b5c21c223cf6e6072ffb0bf6aeddab8e65d3c8bb2aade1 november:10fd29e6e0abf1ebd84908fe9dfee2c7626124722a77bad240dd709fd2da6d1d october:ddc66462e08cc8feaf15b6b4055e9f27d52ca60af96f67a6ad94e112a625623e
2025 april:f939b6e5336f815e20bf8535f72984c520493f27bd55be1d9b39016fe6e2b32c december:81a47446904245c85aa2acf89f1b9d3a27884424470b870bc9c15bc7f2bb8255 february:6b8b4fb93a8a4ad480e768cae85ef6376301cba7c8b907ceb3194e8b869f18fe january:7adb088d7b3c47701f28032c52bf0e88c16d3b693974ea4f167d574d51664f01 march:016a10fce0ff270f0ad5a7f6d5d92c0f01b0a3a115a259705cedd3f66a71a907 november:238384fb3d3dedfa9512fe6375164a03e1aef123ba30365c5f14ea0004e7e419 october:34d33d33b25d8f4331ad2ed5c0b2f4ed295f6bf452257bdb2e4e8ade157f48b0
2026 april:486496f9f87c557e055184495624ad5be3585f3bb1e9642cc231a55cfbff1531 december:10cdb78803185010746a929c58ea8929b914f74e42ac1b0566fbbf0f976cac5d february:9ce90ce8173c1008bccf1180f721fdc21268297407568620b7df2feb9057f190 january:b09ba48c4e1a30439c7f6133ce4d06651c937ce20b25509a56320844259634a0 march:49ecc2eef0db978dacc9df4760689fe09e9e608ae32aa0ed9a1cfbf5543da1c7 november:e66964624aebbc8b02cb401f1c1b30bf6b9f3400ce68adacdcf57a8555183ca0 october:5e19280b0f68153fee40315f8071a1115278ca15a369de8c61d07abbf4655a65
```

The 23 checksum-verified historical reconciliation pages are under
`raw/reconciliation/bref/`. Their game ID, direct immutable URL, relative
path, and SHA-256 are recorded in
`sources.basketball_reference.historical_boxscores` in the manifest. They are
preserved and recovered through the verified raw snapshot; the normal
season-fetch command does not re-download these one-off audit pages.

Curated-reference hashes: `reference/franchises.csv`
`aae69415cee458165e589355d8e6d34c248c9f1849586fb1c75ee3063c3028cb`;
`reference/game_corrections.csv`
`4fe5af540239bb1dfb1b08b99e256dfd88a8fe05531c092256a633a19c84e6e4`;
`reference/hoopr_snapshot.json`
`167dfaa6e1d949e9f4812687ff74f2316610b1f016b070fd5bb4151e857ff1dc`;
and `reference/venue_capacities.csv`
`7fcd2cfee1485d60541e77b89e6817f37fd6d18261b5680a5442a4bbc4bdbb45`.
The manifest also supplies every b-ref page's immutable URL. Print its
complete machine-readable ledger without touching the raw cache with:

```bash
jq '.sources.basketball_reference, .references' data/parquet/build_manifest.json
```

## Commands and safe source access

```bash
# Cache all providers, or one ending season (2020..2026).
.venv/bin/python -m nba_data fetch [--season 2024]

# Produce the gitignored venue-era inventory used to curate capacities.
.venv/bin/python -m nba_data inspect-venues

# Validate and atomically publish the four Parquet products plus manifest.
.venv/bin/python -m nba_data build
```

The original NBA Stats failure was an **AdGuard DNS sinkhole**, not a TLS
problem: `stats.nba.com` resolved to `0.0.0.0`, sending curl to a local
listener. Before a fetch, the client resolves the hostname and rejects an
empty, unspecified (`0.0.0.0`/`::`), or loopback answer with an AdGuard query
log/allowlist diagnostic. Correct the DNS/AdGuard policy, then retry with
ordinary certificate validation; TLS bypasses are not an approved remedy.

## Recovery snapshot

After a successful build, archive the untracked raw cache exactly once:

```bash
bash scripts/archive_raw_snapshot.sh
```

The script derives a unique NAS destination from `games.max_season` and the
first 12 hex characters of the manifest SHA-256, refuses if that path already
exists, copies the manifest and raw tree, writes and verifies `SHA256SUMS`,
then uses checksum-based rsync dry-run comparison. It never overwrites a prior
snapshot. If `data/raw/` is lost, run `bash scripts/restore_raw.sh`. The
restore command derives the exact snapshot from the committed manifest,
verifies that the snapshot manifest and complete `SHA256SUMS` inventory match,
stages all raw sources (including legacy inputs and reconciliation pages), and
refuses to replace an existing raw directory.
