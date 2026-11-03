# Player Games Release Boundary Amendment — Design

*Approved 2026-08-03 after the complete 1984–2026 player-log source audit.
This amendment narrows the first trusted `player_games` publication to ending
seasons 2012–2026 while retaining the complete 1984–2026 raw archive and its
diagnostic evidence. It governs wherever it conflicts with the 2026-07-31
roadmap, the 2026-08-02 player-games design, or the 2026-08-02 implementation
plan.*

## Decision

Publish trusted player-appearance/workload rows only for completed ending
seasons 2012–2026. Do not approve or commit the generated 662-point / 1,211-
minute candidate ledger, widen the minute tolerance, impute player values, or
publish the 1984–2011 rows as validated workload.

Keep all 93 attested `LeagueGameLog` body/sidecar pairs for ending seasons
1984–2026. The earlier 1984–2011 responses remain quarantined source evidence
for a future, separately designed reconciliation slice; they are not inputs to
the first trusted publication.

## Evidence that changed the boundary

The deterministic full-cache review reproduced 1,082,012 raw player rows,
52,529 games, and 105,058 team-game sides. Coverage and the validation
implementation were correct, but the review produced 1,873 section entries:

- 662 point entries and 1,211 minute entries;
- 1,300 unique team-game keys across the two sections;
- 1,164 unique games; and
- 573 team-game keys present in both sections.

The point entries split losslessly into 627 incomplete player payloads, 27
erroneous regular-season score references, seven phantom zero-minute scoring
rows, and one isolated complete-minute player-line discrepancy. The minute
entries split into 573 point-deficit overlaps, 264 team sides with positive
box activity on a `MIN=0` row, 228 otherwise clean residual-six-to-eight
entries, and 146 other anomalies. These are source incompleteness, reference
errors, corrupt fields, and possible integerization tails—not one approvable
exception class.

There are no point or minute candidates in ending seasons 2012–2026 under the
existing strict gates. The sole candidate after ending season 2007 is a
234-minute Dallas side in ending season 2011. Therefore 2012 is the first
evidence-backed boundary with zero known aggregate discrepancies.

The exact diagnostic artifacts are:

- `.superpowers/sdd/2026-08-02-player-games-workload/task-6-points-candidate-diagnostic.md`;
- `.superpowers/sdd/2026-08-02-player-games-workload/task-6-minutes-candidate-diagnostic.md`; and
- `.superpowers/sdd/2026-08-02-player-games-workload/task-6-validation-assumption-diagnostic.md`.

The deterministic generated artifacts remain uncommitted:

- source audit SHA-256
  `3e62e215555898ddade63322688760c24ba6c3a5d2c7f4b0face1169fd47dbd0`;
- candidate JSON SHA-256
  `ade10d5af96f47ba633b8526488eb9cb94607ce024aa0ce7e0b63ed9b05d3388`.

## Two explicit scopes

The implementation must stop using one `SEASONS` value for two different
contracts.

### Source archive and diagnostic scope

- `SOURCE_SEASONS` is exactly the integers 1984 through 2026.
- Fetch remains serial, resumable, and capable of all 93 identities: 43
  regular, 43 playoff, and seven play-in responses.
- The default source-review command continues to validate and audit all 93
  pairs and reproduce the historical candidate counts. It does not convert
  those candidates into approvals.
- The exact 36-row matchup ledger remains authoritative for malformed source
  rows in ending seasons 1997–2000. The exact ten-game designation ledger
  remains authoritative for neutral games in ending seasons 2025–2026.
- The raw archive remains immutable, attested, and recoverable even though its
  pre-2012 rows are quarantined from publication.

### Trusted publication scope

- `PUBLICATION_SEASONS` is exactly the integers 2012 through 2026.
- The publication consumes 37 cache identities: 15 regular, 15 playoff, and
  seven play-in responses.
- It writes exactly 15 partitions, `season=2012` through `season=2026`.
- Regular and postseason coverage comparisons are restricted exactly to those
  15 ending seasons. Extra reference rows outside the publication scope do not
  participate in an outer-join failure.
- The committed point/minute exception ledger remains exactly
  `{"minutes": {}, "points": {}}`.
- Publication requires zero observed point exceptions and zero observed minute
  exceptions. An observed discrepancy or a non-empty/stale ledger entry fails.
- The current strict minute targets, five-minute residual limit, cross-team
  overtime agreement, and postseason published-overtime agreement remain
  unchanged.
- The publication applies zero of the 36 historical malformed-matchup repairs
  and all ten 2025–2026 designation repairs. Both full canonical ledger files
  remain stable inputs and are bound into the manifest; validation records the
  release-scoped applied repair sets.

## Data flow and fail-closed boundaries

`player_logs` owns source scope. Its fetch and complete-inventory validators
continue to use `SOURCE_SEASONS`. It also exposes the publication season tuple
without changing the accepted fetch boundary, so an operator may still fetch
or revalidate a quarantined season.

`player_games_build` owns publication scope. Shared assembly and coverage
helpers take an explicit season tuple rather than consulting a module-global
ambiguous range. The source-review path passes `SOURCE_SEASONS`; the build,
manifest recomputation, independent publication validator, final audit, and
partition inventory pass `PUBLICATION_SEASONS`.

The full source-review path emits the known historical candidates and never
calls them approved. The publication path filters before team aggregation and
coverage validation, observes empty discrepancy dictionaries, and validates
those dictionaries against the committed empty ledger. Historical candidates
must not be computed and then suppressed after validation.

The build requires the complete 93-pair raw inventory to remain structurally
valid before publication. Only the 37 consumed publication pairs appear in the
publication manifest's `sources.player_logs` binding and cache-entry count, so
the manifest states exactly which bytes affected the released rows.

## Manifest, audit, and recovery

The manifest retains its existing nine top-level keys. Its scope lists only
2012–2026, its partition map contains exactly 15 entries, and
`sources.player_logs` contains exactly 37 body/sidecar bindings. Validation
reports zero point exceptions, zero minute exceptions, zero applied matchup
repairs, and ten applied designation repairs with their existing deterministic
digest contracts.

The final committed audit must state both scopes without conflating them:

- raw archive: 1984–2026, 93 cache identities;
- trusted publication: 2012–2026, 37 consumed identities, 15 partitions;
- historical quarantine: 1984–2011, with the exact full-audit counts and
  diagnostic/candidate hashes above; and
- no approved point/minute exceptions.

The immutable snapshot retains the complete 93-pair raw cache plus the
15-partition publication. Snapshot validation has two independent gates:

1. the raw tree is the exact, attested 93-pair source archive and every file is
   present in `SHA256SUMS`; and
2. the publication independently validates its exact 2012–2026 manifest and
   binds only the 37 consumed pairs.

A corrupt or incomplete historical raw pair fails archive validation. A
historical raw byte is not allowed to appear as a consumed publication source.
Restore continues to refuse an existing target and revalidates both gates.

## Documentation semantics

Documentation must call 2012–2026 the trusted player workload publication.
It must describe 1984–2011 as quarantined raw source evidence, not as released
workload and not as an accepted exception population. Candidate absence in the
release window proves consistency with the current aggregate gates; it does
not prove that no zero-point or otherwise undetectable appearance is absent.

The final audit incorporates the three Task 6 diagnostic summaries so the
quarantine decision remains reviewable after the git-ignored SDD workspace is
removed. The 805,637-byte candidate JSON remains generated evidence rather
than a committed validation ledger.

## Historical remediation boundary

Publishing any ending season before 2012 requires a new design. That design
must treat causes separately: correct the 27 bad regular references, quarantine
or exactly correct the seven phantom rows, reconcile the 627 incomplete team
sides against a licensed independent player-box source, resolve the isolated
Indiana line, and prove any integerization rule without hiding point deficits,
active-zero corruption, cross-team overtime conflicts, or postseason overtime
conflicts.

No historical remediation, alternate-source acquisition, schema quality flag,
or corrected player-row synthesis is authorized by this amendment.

## Acceptance criteria

1. Source fetch, inventory validation, and full audit still cover exactly 93
   identities for ending seasons 1984–2026.
2. The full audit deterministically reproduces 662 point and 1,211 minute
   candidates and retains the approved 36 matchup and ten designation repairs.
3. Publication assembly, coverage, manifest, validation, and partition walking
   cover exactly ending seasons 2012–2026 and exactly 37 consumed identities.
4. The committed point/minute ledger remains empty and the publication observes
   zero point and zero minute discrepancies.
5. The publication contains exactly 15 deterministic Parquet partitions; two
   builds produce byte-identical partitions and manifest.
6. The publication applies zero matchup repairs and exactly ten designation
   repairs, with exact stale/drift rejection.
7. The archive and disposable restore validate all 93 raw pairs independently
   from the 37-pair publication binding.
8. Final documentation names both scopes and records the quarantine evidence;
   it never describes 1984–2011 as validated workload.
