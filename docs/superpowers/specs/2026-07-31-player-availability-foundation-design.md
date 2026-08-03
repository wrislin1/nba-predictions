# Player Availability & Workload Foundation (1984–2026) — Roadmap Design

*Originally approved 2026-07-31. Revised 2026-08-02 after an evidence-backed
repository and source review. Amended 2026-08-03 after the complete player-log
audit narrowed the first trusted D1 publication to ending seasons 2012–2026
while preserving 1984–2011 as quarantined raw evidence.*

## Goal

Support three later analyses—rest and performance, schedule compression and
injuries, and load-management trends—without pretending that one source can
answer every availability question. The roadmap now distinguishes three
capabilities:

1. **D1 — player appearances and workload:** who appeared and what box-score
   workload they recorded.
2. **D2 — injury evidence and roster eligibility:** why a player may have been
   unavailable, and whether that player belonged in the population at risk.
3. **D3 — schedule exposure:** rest, game density, and travel proxies.

These are separate data contracts, source risks, and implementation plans.
Completing D1 does not establish absences; completing D2 without a roster
denominator does not establish availability; completing D3 does not measure a
team's actual travel itinerary.

## Approved decomposition

| Slice | Deliverable | Status | Dependency |
|---|---|---|---|
| D1 | Trusted `player_games`, ending seasons 2012–2026; quarantined raw evidence for 1984–2011 | Release-boundary amendment approved | Existing regular-season and postseason publications |
| D3 | Exact schedule-feature functions and location references | Separate future design | A decision on regular-only versus all-game scope, plus a neutral-site location audit |
| D2 | Qualified injury-event source plus an explicit roster/eligibility denominator | Blocked pending source qualification and framing decision | Legal/technical source access, stable event identity, and roster semantics |

D1 is first because it has a verified bulk source, creates a useful workload
dataset by itself, and does not depend on the unresolved injury-source or
location questions. D3 is second because it is deterministic once its scope
and location truth are pinned. D2 is last because its source and denominator
are both unresolved.

The complete 1984–2026 audit subsequently proved that source availability is
not the same as trustworthy historical workload. The 2026-08-03
[`player-games release-boundary amendment`](2026-08-03-player-games-release-boundary-amendment-design.md)
therefore narrows the first publication without discarding the acquired raw
evidence.

## Review findings that changed the original design

### D1 source availability is real, but the original validation claims were not

Direct `LeagueGameLog` probes on 2026-08-02 established that the endpoint
returns player rows for the full requested boundary:

- ending season 1984 regular season: 19,544 rows across 943 games;
- ending season 1984 playoffs: 1,616 rows across 79 games; and
- ending season 2026 play-in: 126 rows across 6 games.

The same 1984 regular-season probe disproved two proposed exact gates. Player
point totals disagreed with the committed `team_games` total for 29 of 1,886
team-games, and one team-game summed to 277 reported player-minutes rather than
lying near a `240 + 25 × overtime_periods` total. The later complete audit
expanded those findings to 662 point and 1,211 minute entries and proved that a
blanket exception ledger would hide multiple source and reference failure
modes. The first trusted publication therefore begins in 2012; the historical
entries remain diagnostic evidence and must not be hidden by a wide tolerance
or silently corrected.

The committed `games.parquet` and `team_games.parquet` products contain regular
season only. Postseason coverage must therefore cross-check against
`playoff_games.parquet` by the source-independent game identity
`(season, game_date, game_type, home_franchise_id, away_franchise_id)`, not by
NBA Stats `game_id` and not by vague series-size bounds.

### D1 does not provide a roster denominator

`LeagueGameLog` contains players credited with an appearance, including rare
zero-minute rows. A gap between surrounding appearances is an
**observed-appearance gap**, not proof that the player was on the active roster,
eligible, or expected to play. It cannot locate a stint boundary for a player
who never appeared during a ten-day contract, and it cannot distinguish
injury, rest, suspension, assignment, or coach's decision.

Accordingly, D1 is scoped to appearances and workload. Absence inference and
roster stints were removed from D1. A later D2 design must either add a
provenance-backed roster/transaction source or explicitly narrow its claims to
injury-event evidence without an availability denominator.

### D2 is not ready for an implementation plan

On 2026-08-02, Pro Sports Transactions returned HTTP 403 from this environment
for both the basketball search page and `robots.txt`. No browser automation,
proxy, copied data dump, or other workaround is approved or designed here.
Before PST can be selected, a source-qualification slice must establish:

1. readable terms and robots policies;
2. permitted automated access from the implementation environment;
3. a polite request budget and stable pagination contract;
4. stable source-row identity independent of page number or page ordinal;
5. whether a source row can contain multiple player movements; and
6. a deterministic, evidence-backed person-ID resolution policy.

The future design must compare PST with official NBA injury-report PDFs for the
COVID-era analyses. PST may provide broader historical evidence, but its
volunteer-compiled and asymmetric coverage cannot by itself prove that an
unreported injury did not occur.

### D3 needs its own semantic design

The original feature names were not precise enough for an implementation plan.
The D3 design must decide and test all of the following before code is written:

- whether features cover regular-season games only or also join postseason
  rows that use a different source ID;
- whether `games_in_last_7` includes the current game and which calendar window
  it represents;
- whether `is_3_in_4` means at least three games in the inclusive four-day
  window ending on the current game;
- whether `road_trip_game_number` is renamed to the defensible
  `consecutive_away_game_number` or backed by actual trip evidence;
- how `travel_km` handles off-day returns home, neutral sites, and missing
  locations; and
- whether `tz_shift` means IANA-zone UTC-offset change on the game date or a
  simpler city-zone label change.

Current neutral-site truth is concentrated in ending seasons 2020 and later.
A franchise-city table alone cannot locate earlier international or neutral
games. D3 therefore needs a neutral-game location audit and an exact override
reference before it can claim 1984–2026 travel coverage.

## Shared constraints

- **Era and cutoff:** D1 retains raw source evidence for completed ending
  seasons 1984–2026, but its first trusted publication is fixed to ending
  seasons 2012–2026. It does not implement a mutable in-progress season.
- **Provenance:** raw response bodies and SHA-256 sidecars are immutable inputs;
  normalized publications bind every consumed body and sidecar.
- **Storage:** large raw caches and partitioned Parquet publications remain
  gitignored under `data/raw/` and `data/parquet-large/`. Code, exact exception
  ledgers, deterministic manifests/audits, and documentation are committed.
- **Recovery:** each large-data slice gets an immutable, manifest-addressed NAS
  snapshot under `/mnt/nas/llm/documents/nba-predictions-archive/` plus tested
  refusal-to-overwrite restore behavior.
- **Missingness:** missing is never zero. Source disagreements are preserved and
  reviewed rather than imputed away.
- **Analysis boundary:** no causal or trend analysis is part of these foundation
  slices. Each analysis still requires its own design after its prerequisites
  exist.

## D1 handoff

The approved D1 contract is defined in
[`2026-08-02-player-games-workload-design.md`](2026-08-02-player-games-workload-design.md).
Its implementation plan is
[`../plans/2026-08-02-player-games-workload.md`](../plans/2026-08-02-player-games-workload.md).

## Roadmap success criteria

1. D1 publishes a reproducible, source-attested player-appearance/workload
   table for every regular, playoff, and play-in game in ending seasons
   2012–2026 with zero point/minute exceptions, while preserving the exact
   1984–2026 raw archive and quarantining 1984–2011 from workload publication.
2. D3 receives a separately approved design with exact temporal-window,
   location, neutral-site, postseason, and timezone semantics before it gets an
   implementation plan.
3. D2 proceeds only after source access is proven and the user chooses between
   a true roster/eligibility denominator and the narrower injury-evidence-only
   claim.
4. Later analyses state which of D1–D3 they require and inherit each source's
   documented missingness and coverage limits.
