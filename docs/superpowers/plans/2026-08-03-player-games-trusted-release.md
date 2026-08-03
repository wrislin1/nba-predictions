# Player Games Trusted Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the player-games foundation by preserving the exact 1984–2026 raw source archive while publishing and recovery-testing only the discrepancy-free 2012–2026 trusted workload.

**Architecture:** Split the old shared season range into explicit source and publication contracts. Fetch, complete-inventory validation, and the diagnostic audit continue across all 93 raw identities; build, coverage, manifest, publication validation, and partitions use only the 37 identities in ending seasons 2012–2026. Snapshot validation then proves the full 93-pair raw archive and the narrower 15-partition publication as two independent gates.

**Tech Stack:** Python 3.12+, pandas, PyArrow/Parquet, pytest, Bash, rsync, SHA-256, immutable raw JSON/sidecar caches, deterministic JSON manifests.

## Global Constraints

- Governing amendment: `docs/superpowers/specs/2026-08-03-player-games-release-boundary-amendment-design.md`.
- Work only in the existing isolated worktree `/home/riz/dev/nba-predictions-revived/.worktrees/player-games-workload` on branch `feat/player-games-workload`.
- Stop at an unmerged, unpushed branch ready for the user's separate-session review. Do not merge, push, deploy, remove the worktree, or start D2/D3.
- `SOURCE_SEASONS` is exactly `tuple(range(1984, 2027))`; `PUBLICATION_SEASONS` is exactly `tuple(range(2012, 2027))`; `PLAY_IN_SEASONS` remains exactly `tuple(range(2020, 2027))`.
- Source acquisition, exact inventory validation, diagnostic audit, archive, and restore cover all 93 identities: 43 regular, 43 playoff, and seven play-in body/sidecar pairs.
- Publication consumes exactly 37 identities: 15 regular, 15 playoff, and seven play-in pairs; it writes exactly 15 partitions for ending seasons 2012–2026.
- `src/nba_data/reference/player_game_validation_exceptions.json` remains exactly `{"minutes": {}, "points": {}}`; do not copy the generated candidate ledger.
- Publication observes zero point and zero minute discrepancies. Do not widen `MINUTE_TOLERANCE = 5`, impute values, drop rows, or suppress historical discrepancies after validation.
- The full diagnostic audit must continue to reproduce 1,082,012 raw player rows, 52,529 games, 105,058 team-game sides, 662 point candidates, 1,211 minute candidates, 1,300 affected team-game keys, 1,164 affected games, 573 cross-section overlaps, 36 matchup repairs, and ten designation repairs from the current attested inputs.
- The committed quarantine summary preserves the lossless cause counts: point candidates split 627 / 27 / 7 / 1, and minute candidates split 573 / 264 / 228 / 146, using the approved labels from the governing amendment.
- The current source-audit SHA-256 is `3e62e215555898ddade63322688760c24ba6c3a5d2c7f4b0face1169fd47dbd0`; candidate JSON SHA-256 is `ade10d5af96f47ba633b8526488eb9cb94607ce024aa0ce7e0b63ed9b05d3388`.
- Publication applies zero historical matchup repairs and exactly ten 2025–2026 designation repairs. Keep both full canonical ledger files bound into the manifest and preserve exact stale/drift rejection.
- The manifest retains exactly nine top-level keys and binds only the 37 consumed player-log bodies/sidecars. The raw snapshot independently retains all 93 pairs.
- Generated files under `data/raw/**`, `data/parquet-large/**`, and `/tmp/player-game-*` are never committed.
- Follow strict TDD for behavior changes: add a real failing test, observe the expected failure, implement minimally, then rerun the narrow and full suites.
- Preserve unrelated user changes. If an unexpected file appears, identify its creator before cleanup and remove only confirmed self-created artifacts.

---

## File Structure

`src/nba_data/player_logs.py`
: Owns `SOURCE_SEASONS`, `PUBLICATION_SEASONS`, source identity enumeration, fetch boundaries, and the exact 93-pair inventory contract.

`src/nba_data/player_games_build.py`
: Accepts explicit season scopes for assembly and coverage; keeps the full audit diagnostic-only; builds, validates, and renders the 2012–2026 publication.

`src/nba_data/reference/player_game_validation_exceptions.json`
: Remains the canonical empty point/minute ledger; it is verified but not modified.

`src/nba_data/player_games_snapshot.py`
: Validates the full raw archive and trusted publication, pins the manifest hash, and independently rebuilds the publication.

`scripts/archive_player_games_snapshot.sh`
: Creates an immutable, manifest-addressed NAS snapshot containing all 93 raw pairs and the 15-partition publication.

`scripts/restore_player_games_snapshot.sh`
: Restores only into absent targets, rebuilds from restored raw evidence, and proves byte identity before placement.

`scripts/audit_player_logs.py`
: Continues to render the full diagnostic source review and writes the final trusted-release audit from verified artifacts.

`tests/test_player_logs.py`
: Protects the source/publication identity split without narrowing historical fetch support.

`tests/test_player_games_build.py`
: Protects explicit coverage scope, empty-ledger behavior, 37-source manifests, 15 partitions, repair counts, deterministic builds, and final-audit content.

`tests/test_player_games_snapshot.py`
: Exercises snapshot layout, independent rebuild, archive, restore, mutation, capacity, and refusal behavior with real temporary files.

`README.md`, `docs/data-dictionary.md`, `docs/audits/2026-08-02-player-games-source-audit.md`
: Describe the trusted release, separate raw archive, historical quarantine, commands, recovery proof, and limitations.

`tests/test_repository_layout.py`, `scripts/verify_repository.py`, `tests/test_verify_repository.py`
: Prevent missing documentation/scripts and accidental tracking of raw or large generated data.

---

### Task 1: Split source and publication season identities

**Files:**
- Modify: `src/nba_data/player_logs.py:25-26, 481-615, 683`
- Modify: `tests/test_player_logs.py:165-180, 340-375, 925-955`

**Interfaces:**
- Consumes: existing game-type mapping, cache-path rules, fetch implementation, and exact source metadata contract.
- Produces: `SOURCE_SEASONS`, `PUBLICATION_SEASONS`, `PLAY_IN_SEASONS`, and `cache_identities(seasons: Iterable[int] | None = None) -> tuple[tuple[int, str], ...]` for Tasks 2–5. `None` resolves to the current `SOURCE_SEASONS`, preserving default behavior while allowing tiny explicit-scope tests.

- [ ] **Step 1: Write failing boundary and identity-enumeration tests**

Add source-shaped behavioral tests whose hand-derived expectations catch accidental narrowing of acquisition and accidental widening of publication:

```python
def test_cache_identities_separate_source_archive_from_publication():
    source = player_logs.cache_identities()
    publication = player_logs.cache_identities(
        player_logs.PUBLICATION_SEASONS
    )

    assert len(source) == 93
    assert source[:2] == ((1984, "regular"), (1984, "playoff"))
    assert source[-3:] == (
        (2026, "regular"),
        (2026, "playoff"),
        (2026, "play_in"),
    )
    assert len(publication) == 37
    assert publication[:2] == ((2012, "regular"), (2012, "playoff"))
    assert publication[-3:] == source[-3:]


def test_source_helpers_still_accept_quarantined_1984():
    assert player_logs.season_label(1984) == "1983-84"
    assert player_logs.cache_path(Path("/data"), 1984, "regular") == (
        Path("/data/raw/stats_nba/player_logs/season=1984/regular.json")
    )


def test_cache_identities_reject_season_outside_source_archive():
    with pytest.raises(
        player_logs.PlayerLogValidationError,
        match="1984..2026",
    ):
        player_logs.cache_identities((2012, 2027))
```

Update the existing exact-inventory test to build its fixture from
`cache_identities()` and retain its literal `len(inventory) == 93` assertion.

- [ ] **Step 2: Run the narrow tests and observe the intended RED state**

```bash
.venv/bin/python -m pytest \
  tests/test_player_logs.py::test_cache_identities_separate_source_archive_from_publication \
  tests/test_player_logs.py::test_source_helpers_still_accept_quarantined_1984 \
  tests/test_player_logs.py::test_cache_identities_reject_season_outside_source_archive \
  -q
```

Expected: the first and third tests fail because `cache_identities`,
`SOURCE_SEASONS`, and `PUBLICATION_SEASONS` do not exist; the 1984 control
passes and proves the failure is the missing split, not a broken fixture.

- [ ] **Step 3: Implement the explicit source/publication contract**

Replace the ambiguous constant and centralize identity enumeration:

```python
SOURCE_SEASONS = tuple(range(1984, 2027))
PUBLICATION_SEASONS = tuple(range(2012, 2027))
PLAY_IN_SEASONS = tuple(range(2020, 2027))


def cache_identities(
    seasons: Iterable[int] | None = None,
) -> tuple[tuple[int, str], ...]:
    if seasons is None:
        seasons = SOURCE_SEASONS
    ordered_seasons = tuple(dict.fromkeys(seasons))
    unsupported = tuple(
        season for season in ordered_seasons if season not in SOURCE_SEASONS
    )
    if unsupported:
        raise PlayerLogValidationError(
            f"ending season must be in 1984..2026: {unsupported}"
        )
    return tuple(
        (season, game_type)
        for season in ordered_seasons
        for game_type in applicable_game_types(season)
    )
```

Make `season_label`, ledger season validation, `validate_cache_inventory_root`,
and `fetch` use `SOURCE_SEASONS` or `cache_identities()` as appropriate. Use
`fetch(data_dir, seasons: Iterable[int] | None = None)` and resolve `None` to
the current `SOURCE_SEASONS`; keep the error text `1984..2026`. Remove
`SEASONS`; do not leave a compatibility alias that could recreate the
ambiguity.

- [ ] **Step 4: Run the player-log suite and verify GREEN**

```bash
.venv/bin/python -m pytest tests/test_player_logs.py -q
```

Expected: all player-log tests pass; the inventory test still reports 93
identities and 1984 remains accepted.

- [ ] **Step 5: Verify formatting and commit Task 1**

```bash
git diff --check
git add src/nba_data/player_logs.py tests/test_player_logs.py
git commit -m "refactor: split player log season scopes"
```

Expected: the commit contains only source-scope code and tests; the empty
point/minute ledger and generated data are absent.

---

### Task 2: Scope assembly, coverage, and exception validation explicitly

**Files:**
- Modify: `src/nba_data/player_games_build.py:22-23, 364-405, 772-825, 889-1000, 1383-1455`
- Modify: `tests/test_player_games_build.py:190-220, 830-1120, 1340-1680`
- Verify unchanged: `src/nba_data/reference/player_game_validation_exceptions.json`
- Generate, never commit: `/tmp/player-game-source-review-scope-check/**`

**Interfaces:**
- Consumes: `player_logs.SOURCE_SEASONS`, `player_logs.PUBLICATION_SEASONS`, and `player_logs.cache_identities()` from Task 1.
- Produces: explicit `seasons` parameters on shared assembly/coverage helpers; full `audit_source(data_dir)` behavior; publication-scoped `_audit_from_roots` behavior returning only consumed body paths.

- [ ] **Step 1: Write failing coverage-scope tests**

Reuse the existing `_player_game_rows()` and `_coverage_references()` fixtures,
which contain 1984 regular/playoff games and a 2020 play-in game. Restrict the
call to 2020; the old hard-coded range returns all six sides, while the explicit
scope returns the two literal 2020 sides:

```python
def test_cross_validate_coverage_uses_explicit_publication_seasons():
    aggregates = player_games_build.aggregate_team_games(_player_game_rows())
    games, team_games, playoffs = _coverage_references()

    validated = player_games_build.cross_validate_coverage(
        aggregates,
        games,
        team_games,
        playoffs,
        seasons=(2020,),
    )

    assert set(validated["season"]) == {2020}
    assert len(validated) == 2
```

The expected identities are LAL and POR, and their published totals are the
fixture literals 126 and 122.

Add these independent behavior tests:

```python
def test_assemble_seasons_reads_only_the_requested_scope(monkeypatch, tmp_path):
    requested = []

    def fake_assemble(_data_dir, season, *args, **kwargs):
        requested.append(season)
        return pa.table({"season": [season]})

    monkeypatch.setattr(player_games_build, "_assemble_season", fake_assemble)
    player_games_build._assemble_seasons(
        tmp_path,
        seasons=(2012, 2013),
        matchup_exceptions={},
        designation_exceptions={},
    )
    assert requested == [2012, 2013]


@pytest.mark.parametrize(
    ("section", "entry"),
    [
        ("points", _points_exception()),
        ("minutes", _minutes_exception()),
    ],
)
def test_publication_scope_rejects_stale_nonempty_ledger(
    monkeypatch,
    tmp_path,
    section,
    entry,
):
    raw_root, references, ledger = _seed_build_inputs(monkeypatch, tmp_path)
    document = _ledger()
    document[section]["0028300001|BOS"] = entry
    _save_ledger(ledger, document)
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match="stale.*0028300001\\|BOS",
    ):
        player_games_build.build_from_roots(raw_root, references, stage)
```

Use the existing real builder helpers rather than mocking aggregation or
exception comparison. Patch only file-path constants needed to point at the
temporary canonical ledger/reference fixtures.

Change `_seed_build_inputs` to write the canonical empty ledger rather than
calling `_write_reviewed_fixture_ledger`; adjust its source-shaped minute
fixture so the ordinary build observes no discrepancy. `_use_tiny_scope` and
`_use_one_season_scope` patch both `SOURCE_SEASONS` and
`PUBLICATION_SEASONS`, while production constants remain untouched.

- [ ] **Step 2: Run the new tests and observe the intended RED state**

```bash
.venv/bin/python -m pytest \
  tests/test_player_games_build.py::test_cross_validate_coverage_uses_explicit_publication_seasons \
  tests/test_player_games_build.py::test_assemble_seasons_reads_only_the_requested_scope \
  tests/test_player_games_build.py::test_publication_scope_rejects_stale_nonempty_ledger \
  -q
```

Expected: failures identify the missing `seasons` keyword and the old shared
scope; no failure may come from malformed fixture data.

- [ ] **Step 3: Thread the explicit scope through assembly and evidence checks**

At module level define:

```python
SOURCE_SEASONS = player_logs.SOURCE_SEASONS
PUBLICATION_SEASONS = player_logs.PUBLICATION_SEASONS
PLAY_IN_SEASONS = player_logs.PLAY_IN_SEASONS
```

Use the exact keyword-only parameter
`seasons: tuple[int, ...]` on `cross_validate_coverage` after its four frame
arguments and on `_assemble_seasons` after `data_dir`. Replace the hard-coded
coverage scope with:

```python
scoped = set(seasons)
```

Replace the assembly range and evidence calls with:

```python
tables = [
    _assemble_season(
        data_dir,
        season,
        matchup_exceptions,
        matchup_evidence,
        designation_exceptions,
        designation_evidence,
        franchise_resolver,
    )
    for season in seasons
]
_require_exact_matchup_evidence(
    matchup_exceptions,
    matchup_evidence,
    seasons=seasons,
)
_require_exact_designation_evidence(
    designation_exceptions,
    designation_evidence,
    seasons=seasons,
)
```

Add `seasons: tuple[int, ...]` to `_scoped_matchup_exceptions`,
`_require_exact_matchup_evidence`, `_scoped_designation_exceptions`, and
`_require_exact_designation_evidence`. Every caller passes either
`SOURCE_SEASONS` or `PUBLICATION_SEASONS`; none reads an ambiguous global.

- [ ] **Step 4: Keep the full audit diagnostic-only and the build publication-only**

`audit_source(data_dir)` must validate all 93 pairs and call:

```python
player_games = _assemble_seasons(
    data_dir,
    seasons=SOURCE_SEASONS,
    matchup_exceptions=matchup_exceptions,
    matchup_evidence=matchup_repairs,
    designation_exceptions=designation_exceptions,
    designation_evidence=designation_repairs,
    franchise_resolver=franchise_resolver,
).to_pandas()

validated_team_games = cross_validate_coverage(
    team_aggregates,
    regular_games,
    regular_team_games,
    postseason_games,
    seasons=SOURCE_SEASONS,
)
```

`_audit_from_roots` must first call
`player_logs.validate_cache_inventory_root(raw_root)` to keep the 93-pair
structural precondition, but assemble and validate only
`PUBLICATION_SEASONS`. Filter the returned body tuple by exact identities from
`player_logs.cache_identities(PUBLICATION_SEASONS)` so the returned `bodies`
contains exactly 37 paths. Collect discrepancies only after this filtering and
then call `validate_exceptions(discrepancies, approved)` against the committed
empty ledger.

- [ ] **Step 5: Update existing tiny-scope tests and verify the build suite**

Replace test-only monkeypatches of the removed `SEASONS` name with explicit
helper arguments or `PUBLICATION_SEASONS`/`SOURCE_SEASONS` patches. Preserve
hand-derived expected rows and do not compute expectations through production
scope helpers.

```bash
.venv/bin/python -m pytest tests/test_player_games_build.py -q
```

Expected: all builder tests pass, including existing exception drift, coverage,
matchup, and designation cases.

- [ ] **Step 6: Re-run the real full diagnostic audit**

Require the output directory to be absent before running:

```bash
test ! -e /tmp/player-game-source-review-scope-check
.venv/bin/python scripts/audit_player_logs.py \
  --data-dir data \
  --output-dir /tmp/player-game-source-review-scope-check
sha256sum \
  /tmp/player-game-source-review-scope-check/source-audit.md \
  /tmp/player-game-source-review-scope-check/player_game_validation_exceptions.json
.venv/bin/python -c "import json; from pathlib import Path; p=json.loads(Path('/tmp/player-game-source-review-scope-check/player_game_validation_exceptions.json').read_text()); assert len(p['points'])==662; assert len(p['minutes'])==1211; print({'points':len(p['points']),'minutes':len(p['minutes'])})"
```

Expected: the source-audit and candidate hashes are respectively
`3e62e215555898ddade63322688760c24ba6c3a5d2c7f4b0face1169fd47dbd0`
and
`ade10d5af96f47ba633b8526488eb9cb94607ce024aa0ce7e0b63ed9b05d3388`;
counts remain 662 and 1,211.

- [ ] **Step 7: Confirm the committed ledger is still byte-exact and commit Task 2**

```bash
.venv/bin/python -c "from pathlib import Path; assert Path('src/nba_data/reference/player_game_validation_exceptions.json').read_bytes()==b'{\\n  \\"minutes\\": {},\\n  \\"points\\": {}\\n}\\n'"
git diff --check
git add src/nba_data/player_games_build.py tests/test_player_games_build.py
git commit -m "fix: quarantine historical player workloads"
```

Expected: the byte assertion exits 0; no candidate JSON, raw cache, Parquet,
or ledger change enters the commit.

---

### Task 3: Publish and independently validate the 2012–2026 manifest

**Files:**
- Modify: `src/nba_data/player_games_build.py:1573-1775, 1840-2065, 2074-2110`
- Modify: `tests/test_player_games_build.py:1340-1685, 1900-2245, 2430-2490`
- Generate, never commit: `data/parquet-large/player_games/**`
- Generate, never commit: `/tmp/player-games-first-build.sha256`
- Generate, never commit: `/tmp/player-games-second-build.sha256`

**Interfaces:**
- Consumes: publication-scoped audit and 37 consumed body paths from Task 2.
- Produces: 15 deterministic partitions, a nine-key manifest with exactly 37 source bindings, zero point/minute exceptions, zero matchup repairs, ten designation repairs, and an independent validator that recomputes the same value.

- [ ] **Step 1: Write failing manifest and exact-layout tests**

Extend the real tiny publication fixture so reference inputs contain a
quarantined season plus two publication seasons. Assert literal manifest
values rather than deriving them from module constants:

```python
def test_manifest_binds_only_trusted_publication_scope(tmp_path, monkeypatch):
    stage, manifest = _build_scoped_publication(
        tmp_path,
        source_seasons=(2011, 2012, 2013),
        publication_seasons=(2012, 2013),
    )

    assert manifest["scope"]["ending_seasons"] == [2012, 2013]
    assert set(manifest["partitions"]) == {"2012", "2013"}
    assert len(manifest["sources"]["player_logs"]) == 4
    assert {
        entry["identity"]["season"]
        for entry in manifest["sources"]["player_logs"]
    } == {2012, 2013}
    assert manifest["validation"]["cache_entries"] == 4
    assert manifest["validation"]["exceptions"] == {
        "minutes": 0,
        "points": 0,
    }
    assert manifest["validation"]["matchup_repairs"]["count"] == 0


def test_publication_layout_rejects_quarantined_partition(tmp_path, monkeypatch):
    root, _manifest = _build_scoped_publication(
        tmp_path,
        source_seasons=(2011, 2012),
        publication_seasons=(2012,),
    )
    (root / "season=2011").mkdir()
    with pytest.raises(
        player_games_build.PlayerGamesBuildError,
        match="extra=.*season=2011",
    ):
        player_games_build.validate_publication(
            tmp_path / "raw/stats_nba/player_logs",
            root,
            tmp_path / "parquet",
        )
```

Add an independent validation tamper test that inserts a 2011 source binding
into stored manifest JSON, rewrites canonical bytes, and expects
`manifest source mismatch`.

Implement test helper `_build_scoped_publication` by patching
`SOURCE_SEASONS` and `PUBLICATION_SEASONS` in `player_logs` and
`player_games_build`, calling the existing `_publish_scope` and
`_publish_references` for the supplied source tuple, writing canonical empty
point/minute, matchup, and designation ledgers, building into a newly created
empty stage, and returning `(stage, manifest)`. Its expected values remain the
literal assertions above rather than calls back into production scope helpers.

- [ ] **Step 2: Run the new tests and observe RED**

```bash
.venv/bin/python -m pytest \
  tests/test_player_games_build.py::test_manifest_binds_only_trusted_publication_scope \
  tests/test_player_games_build.py::test_publication_layout_rejects_quarantined_partition \
  -q
```

Expected: failures expose remaining loops/layout checks that still use the old
shared range or include all 93 inventory paths in the manifest.

- [ ] **Step 3: Make manifest construction and validation publication-scoped**

In `_recompute_manifest`, iterate only `PUBLICATION_SEASONS` for partitions and
scope. Pass the 37 consumed `bodies` tuple from Task 2 into `_source_entry`.
Keep the exact nine top-level keys:

```python
expected_top = {
    "schema_version",
    "scope",
    "schema",
    "versions",
    "sources",
    "references",
    "exception_ledger",
    "partitions",
    "validation",
}
```

`_validation_manifest(audit, source_count)` must use the publication rows and
emit literal zero exception counts from the already validated empty
discrepancy dictionaries. `_require_exact_publication_layout` must expect only
`_manifest.json` plus `season=2012` through `season=2026`. Independent
validation must recompute source bindings, partitions, repairs, reference
bindings, and the complete manifest value from disk.

- [ ] **Step 4: Pin release-scoped repair behavior**

Add a real tiny fixture containing one historical approved matchup entry and
one publication-scope approved designation entry. Assert that the tiny
publication applies and digests only its scoped evidence:

```python
assert manifest["validation"]["matchup_repairs"]["count"] == 0
assert manifest["validation"]["designation_repairs"]["count"] == 1
```

The full ledger file bindings remain byte-exact under
`exception_ledger.matchups` and `exception_ledger.designations`. Mutating
either file after build must still make independent validation fail.

- [ ] **Step 5: Run focused and full tests**

```bash
.venv/bin/python -m pytest tests/test_player_games_build.py -q
.venv/bin/python -m pytest -q
git diff --check
```

Expected: all tests pass; there are no warnings or failures.

- [ ] **Step 6: Build the real publication and capture its first inventory**

```bash
.venv/bin/python -m nba_data build-player-games
find data/parquet-large/player_games -type f -print0 \
  | LC_ALL=C sort -z \
  | xargs -0 sha256sum \
  > /tmp/player-games-first-build.sha256
.venv/bin/python -c "import json; from pathlib import Path; p=Path('data/parquet-large/player_games'); m=json.loads((p/'_manifest.json').read_text()); assert m['scope']['ending_seasons']==list(range(2012,2027)); assert len(m['partitions'])==15; assert len(m['sources']['player_logs'])==37; assert m['validation']['cache_entries']==37; assert m['validation']['exceptions']=={'minutes':0,'points':0}; assert m['validation']['matchup_repairs']['count']==0; assert m['validation']['designation_repairs']['count']==10; assert sum(1 for x in p.rglob('*') if x.is_file())==16; print({'partitions':len(m['partitions']),'sources':len(m['sources']['player_logs']),'rows':m['validation']['rows']})"
```

Expected: exactly 16 files (15 Parquet plus `_manifest.json`) and all literal
scope/count assertions pass.

- [ ] **Step 7: Rebuild and prove byte identity**

```bash
.venv/bin/python -m nba_data build-player-games
find data/parquet-large/player_games -type f -print0 \
  | LC_ALL=C sort -z \
  | xargs -0 sha256sum \
  > /tmp/player-games-second-build.sha256
cmp -s /tmp/player-games-first-build.sha256 /tmp/player-games-second-build.sha256
.venv/bin/python -c "from pathlib import Path; from nba_data import player_games_build; root=Path('data'); player_games_build.validate_publication(root/'raw/stats_nba/player_logs', root/'parquet-large/player_games', root/'parquet'); print('publication validation: ok')"
```

Expected: `cmp` exits 0 and independent validation prints
`publication validation: ok`.

- [ ] **Step 8: Commit Task 3 without generated data**

```bash
git status --short
git ls-files data/raw data/parquet-large
git add src/nba_data/player_games_build.py tests/test_player_games_build.py
git commit -m "feat: publish trusted player workloads"
```

Expected: `git ls-files` prints nothing and the commit contains only code and
tests.

---

### Task 4: Validate full-archive snapshots and independent rebuilds

**Files:**
- Create: `src/nba_data/player_games_snapshot.py`
- Create: `tests/test_player_games_snapshot.py`

**Interfaces:**
- Consumes: `player_logs.validate_cache_inventory_root`, `player_games_build.validate_publication`, and `player_games_build.build_from_roots`.
- Produces: `validate_layout(raw_root: Path, publication_root: Path, reference_dir: Path, manifest_sha256: str | None = None) -> dict`, `verify_rebuild(raw_root: Path, publication_root: Path, reference_dir: Path) -> dict`, and CLI subcommands `validate-layout` / `verify-rebuild`.

- [ ] **Step 1: Write failing semantic layout tests**

Create a `snapshot_fixture` pytest fixture containing a complete test-specific
source scope `(1984, 2012, 2013)`, publication scope `(2012, 2013)`, exact canonical
body/sidecar pairs for both regular and playoff types, committed-shape
references, and a real publication produced by `build_from_roots`. It returns
`(raw_root, publication_root, reference_dir, manifest_sha256)`.

Write tests named
`test_validate_layout_checks_full_raw_archive_and_publication`,
`test_validate_layout_rejects_missing_historical_body`,
`test_validate_layout_rejects_changed_publication_partition`,
`test_validate_layout_rejects_manifest_sha_mismatch`, and
`test_validate_layout_rejects_symlink_fifo_and_extra_layout_entries`. Each
test mutates one real file or path shape and asserts the exact failure class
and identifying path.

The independent historical gate is exercised directly:

```python
def test_validate_layout_rejects_missing_historical_body(snapshot_fixture):
    raw_root, publication_root, reference_dir, digest = snapshot_fixture
    historical = raw_root / "season=1984/regular.json"
    historical.unlink()

    with pytest.raises(
        player_games_snapshot.PlayerGamesSnapshotError,
        match="season=1984/regular.json",
    ):
        player_games_snapshot.validate_layout(
            raw_root,
            publication_root,
            reference_dir,
            digest,
        )
```

The missing-historical-body case must remove a raw pair not consumed by the
publication. It proves the archive gate is independent rather than an alias for
manifest validation.

- [ ] **Step 2: Write the failing independent-rebuild test**

```python
def test_verify_rebuild_matches_complete_publication_inventory(snapshot_fixture):
    raw_root, publication_root, reference_dir, _digest = snapshot_fixture
    report = player_games_snapshot.verify_rebuild(
        raw_root,
        publication_root,
        reference_dir,
    )
    assert report["status"] == "ok"
    assert report["publication_files"] == 3  # two tiny partitions + manifest


def test_verify_rebuild_rejects_byte_drift(snapshot_fixture, monkeypatch):
    raw_root, publication_root, reference_dir, _digest = snapshot_fixture
    real_build = player_games_build.build_from_roots

    def build_with_drift(raw, references, stage):
        manifest = real_build(raw, references, stage)
        _rewrite_valid_but_different_parquet(stage)
        return manifest

    monkeypatch.setattr(
        player_games_snapshot.player_games_build,
        "build_from_roots",
        build_with_drift,
    )
    with pytest.raises(
        player_games_snapshot.PlayerGamesSnapshotError,
        match="rebuilt publication differs",
    ):
        player_games_snapshot.verify_rebuild(
            raw_root,
            publication_root,
            reference_dir,
        )
```

Expected inventories are literal sorted `(relative_path, sha256)` pairs; do
not use the production inventory helper to build expected results. Implement
`_rewrite_valid_but_different_parquet` as a test utility that reads one fixture
partition from the newly rebuilt stage, changes one literal player value, and
writes valid Parquet bytes. The monkeypatch injects drift below the snapshot
comparison while keeping the real build path; the helper is never added to
production code.

- [ ] **Step 3: Run and observe RED**

```bash
.venv/bin/python -m pytest tests/test_player_games_snapshot.py -q
```

Expected: collection fails because `nba_data.player_games_snapshot` does not
exist.

- [ ] **Step 4: Implement the semantic validator and CLI**

Use this public shape:

```python
class PlayerGamesSnapshotError(ValueError):
    """Raised when a player-games archive or rebuild violates its contract."""


def validate_layout(
    raw_root: Path,
    publication_root: Path,
    reference_dir: Path,
    manifest_sha256: str | None = None,
) -> dict:
    try:
        _require_regular_tree(raw_root)
        _require_regular_tree(publication_root)
        bodies = player_logs.validate_cache_inventory_root(raw_root)
        manifest_path = publication_root / "_manifest.json"
        observed_manifest_sha256 = hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest()
        if (
            manifest_sha256 is not None
            and manifest_sha256 != observed_manifest_sha256
        ):
            raise PlayerGamesSnapshotError(
                "snapshot manifest SHA-256 mismatch"
            )
        manifest = player_games_build.validate_publication(
            raw_root,
            publication_root,
            reference_dir,
        )
    except PlayerGamesSnapshotError:
        raise
    except (
        OSError,
        player_logs.PlayerLogValidationError,
        player_games_build.PlayerGamesBuildError,
    ) as exc:
        raise PlayerGamesSnapshotError(str(exc)) from exc
    return {
        "status": "ok",
        "archive_cache_entries": len(bodies),
        "publication_cache_entries": len(manifest["sources"]["player_logs"]),
        "publication_partitions": len(manifest["partitions"]),
        "manifest_sha256": observed_manifest_sha256,
    }
```

Walk with `lstat`; permit only real directories and regular files, and make
`_require_regular_tree` identify the offending relative path. When a hash is
supplied, require exact 64-character lowercase hex and equality with the
publication manifest bytes. Normalize filesystem, cache, and publication
validation failures to `PlayerGamesSnapshotError` while retaining the original
exception as the cause, so the CLI and both scripts expose one stable contract.

`verify_rebuild` creates a uniquely owned temporary sibling directory, calls
`build_from_roots`, compares the complete sorted relative-path/SHA-256
inventory with the published tree, and removes only that owned stage in
`finally`. The CLI requires explicit `--raw-root`, `--publication-root`, and
`--reference-dir`; `validate-layout` optionally accepts
`--manifest-sha256`.

- [ ] **Step 5: Run focused tests, module help, and commit Task 4**

```bash
.venv/bin/python -m pytest tests/test_player_games_snapshot.py -q
.venv/bin/python -m nba_data.player_games_snapshot --help
git diff --check
git add src/nba_data/player_games_snapshot.py tests/test_player_games_snapshot.py
git commit -m "feat: validate player game snapshots"
```

Expected: tests pass; help lists `validate-layout` and `verify-rebuild`.

---

### Task 5: Archive and restore the two-scope release atomically

**Files:**
- Create: `scripts/archive_player_games_snapshot.sh`
- Create: `scripts/restore_player_games_snapshot.sh`
- Modify: `tests/test_player_games_snapshot.py`
- Generate, never commit: `/mnt/nas/llm/documents/nba-predictions-archive/player-games-archive-2026-<manifest-sha12>/**`
- Generate, never commit: one disposable restore directory created with `mktemp -d`

**Interfaces:**
- Consumes: Task 4 CLI, the full 93-pair raw root, the trusted publication root, committed references, and exact manifest SHA-256.
- Produces: immutable snapshot `player-games-archive-2026-<manifest-sha12>` containing `raw/stats_nba/player_logs`, `parquet-large/player_games`, and `SHA256SUMS`; `scripts/restore_player_games_snapshot.sh <snapshot-name>` absent-target restore with independent rebuild proof.

- [ ] **Step 1: Write failing archive subprocess tests**

Run the real script path under temporary environment roots. Write tests named
`test_archive_snapshot_copies_full_raw_and_trusted_publication`,
`test_archive_snapshot_is_idempotent_only_for_byte_identical_final`,
`test_archive_snapshot_rejects_different_existing_final`,
`test_archive_snapshot_rejects_stale_stage`,
`test_archive_snapshot_checks_capacity_before_copy`,
`test_archive_snapshot_detects_source_mutation_during_copy`, and
`test_archive_snapshot_signal_leaves_no_final`. Every test asserts the exact
exit status plus filesystem effects; the success test compares a literal
sorted checksum inventory.

The success case asserts the archive contains the test fixture's historical
raw pair even though that pair is absent from the publication manifest.

- [ ] **Step 2: Write failing restore subprocess tests**

Write restore tests named
`test_restore_snapshot_places_raw_and_publication_after_rebuild`,
`test_restore_snapshot_refuses_when_either_target_exists`,
`test_restore_snapshot_refuses_symlink_targets`,
`test_restore_snapshot_detects_snapshot_mutation`,
`test_restore_snapshot_rejects_rebuild_difference`,
`test_restore_snapshot_rolls_back_partial_placement`, and
`test_restore_snapshot_preserves_preexisting_unowned_stages`.

Use one complete subprocess test as the control shape:

```python
def test_restore_snapshot_places_raw_and_publication_after_rebuild(
    archived_snapshot,
    tmp_path,
):
    restore_root = tmp_path / "restore"
    result = _run_restore_script(
        archived_snapshot,
        restore_root,
    )

    assert result.returncode == 0, result.stderr
    assert (restore_root / "raw/stats_nba/player_logs").is_dir()
    assert (
        restore_root / "parquet-large/player_games/_manifest.json"
    ).is_file()
    assert "independent rebuild: ok" in result.stdout
```

Define `archived_snapshot` as a pytest fixture produced by the real archive
script from `snapshot_fixture`. Define `_run_restore_script` as a test-only
`subprocess.run` wrapper that sets the three documented environment variables,
passes `archived_snapshot.name` as the script's sole positional argument,
captures text output, and never uses `check=True`, so each test can assert the
failure code and message.

Assert filesystem effects and exit codes from the real scripts. Mock only the
capacity probe or an intentionally paused copy boundary when necessary; keep
checksum generation, semantic validation, and normal copying real.

- [ ] **Step 3: Run and observe RED**

```bash
.venv/bin/python -m pytest tests/test_player_games_snapshot.py -q
```

Expected: subprocess tests fail because both scripts are absent; Task 4 module
tests remain green.

- [ ] **Step 4: Implement immutable archive orchestration**

The archive script uses `set -euo pipefail`, `LC_ALL=C`, `umask 077`, distinct
EXIT and INT/TERM traps, same-filesystem `mktemp`, and exact environment
defaults:

```bash
NBA_PLAYER_GAMES_ARCHIVE_ROOT=${NBA_PLAYER_GAMES_ARCHIVE_ROOT:-/mnt/nas/llm/documents/nba-predictions-archive}
NBA_PLAYER_GAMES_DATA_DIR=${NBA_PLAYER_GAMES_DATA_DIR:-data}
NBA_PLAYER_GAMES_REFERENCE_DIR=${NBA_PLAYER_GAMES_REFERENCE_DIR:-${NBA_PLAYER_GAMES_DATA_DIR}/parquet}
```

Before copy, run Task 4 `validate-layout` and calculate:

```text
required bytes = allocated source bytes + archive filesystem total bytes / 5
```

Reject stale `.player-games-archive.*` stages. Copy with
`rsync -a --no-times --checksum`, validate the stage, write a sorted
`SHA256SUMS` covering every raw/publication payload file (and excluding the
checksum file itself), revalidate the live source to detect mutation, and
atomically rename to `player-games-archive-2026-<manifest-sha12>`. Never modify
an existing final name; return success only after proving exact byte identity.

- [ ] **Step 5: Implement absent-target restore orchestration**

Require exactly one positional snapshot name matching
`player-games-archive-2026-[0-9a-f]{12}` and resolve it beneath
`NBA_PLAYER_GAMES_ARCHIVE_ROOT` without following symlinks. Pin the manifest
SHA from both snapshot name and manifest bytes. Validate
`SHA256SUMS` and semantic layout before copy. Fail before staging when either
`raw/stats_nba/player_logs` or `parquet-large/player_games` exists or is a
symlink. Stage under each destination parent, revalidate the snapshot after
copy, run Task 4 `verify-rebuild` against the staged raw/publication, and rename
both targets only after every check passes. If the second placement fails,
rename the first target back to its still-owned stage and leave both final
targets absent; the subprocess test injects this boundary failure and proves
the rollback. Cleanup only stage paths whose device/inode identities the script
captured when it created them.

- [ ] **Step 6: Run focused tests and shell syntax checks**

```bash
bash -n scripts/archive_player_games_snapshot.sh
bash -n scripts/restore_player_games_snapshot.sh
.venv/bin/python -m pytest tests/test_player_games_snapshot.py -q
```

Expected: both syntax checks and all snapshot tests pass.

- [ ] **Step 7: Create the real immutable archive**

```bash
scripts/archive_player_games_snapshot.sh
```

Capture the printed snapshot name and manifest SHA. Expected snapshot layout:

```text
player-games-archive-2026-<manifest-sha12>/
  raw/stats_nba/player_logs/        # 93 bodies plus 93 sidecars
  parquet-large/player_games/       # 15 Parquet files plus manifest
  SHA256SUMS
```

- [ ] **Step 8: Verify a disposable restore without overwriting user data**

```bash
player_restore_root="$(mktemp -d)"
NBA_PLAYER_GAMES_DATA_DIR="$player_restore_root" \
NBA_PLAYER_GAMES_REFERENCE_DIR="data/parquet" \
  scripts/restore_player_games_snapshot.sh "$player_snapshot_name"
.venv/bin/python -m nba_data.player_games_snapshot validate-layout \
  --raw-root "$player_restore_root/raw/stats_nba/player_logs" \
  --publication-root "$player_restore_root/parquet-large/player_games" \
  --reference-dir data/parquet
case "$player_restore_root" in
  /tmp/tmp.*) rm -rf -- "$player_restore_root" ;;
  *) echo "refusing to remove unexpected restore root" >&2; exit 1 ;;
esac
```

Expected: restore succeeds into the initially empty directory, reports 93
archive cache entries, 37 publication cache entries, and 15 partitions. Record
the successful rebuild/validation output for Task 6, then remove only the
owned `mktemp` restore root after the explicit path-shape guard succeeds.

- [ ] **Step 9: Commit Task 5 without the snapshot or restore data**

```bash
git status --short
git add scripts/archive_player_games_snapshot.sh scripts/restore_player_games_snapshot.sh tests/test_player_games_snapshot.py
git commit -m "feat: archive trusted player workloads"
```

Expected: only scripts/tests are committed; NAS and temporary restore contents
remain untracked outside the repository.

---

### Task 6: Render the quarantine-aware final audit and documentation

**Files:**
- Modify: `src/nba_data/player_games_build.py:2074-2110`
- Modify: `scripts/audit_player_logs.py:65-105`
- Modify: `tests/test_player_games_build.py:2430-2645`
- Modify: `README.md:80-190`
- Modify: `docs/data-dictionary.md:50-110, 810-end`
- Create: `docs/audits/2026-08-02-player-games-source-audit.md`
- Modify: `tests/test_repository_layout.py`
- Modify: `scripts/verify_repository.py`
- Modify: `tests/test_verify_repository.py`

**Interfaces:**
- Consumes: Task 3 manifest values and Task 5 snapshot/restore proof.
- Produces: final audit with both scopes and historical quarantine evidence; user-facing commands/contracts; repository gates that prevent tracking or documentation regression.

- [ ] **Step 1: Write failing final-audit behavior tests**

Extend `test_render_final_audit_binds_manifest_hash_and_restore` with literal
assertions for the approved semantics:

```python
assert "Trusted publication: ending seasons 2012–2026" in report
assert "Consumed player-log cache identities: 37" in report
assert "Raw archive: ending seasons 1984–2026 (93 identities)" in report
assert "Historical quarantine: ending seasons 1984–2011" in report
assert "Raw player rows audited: 1,082,012" in report
assert "Source games audited: 52,529" in report
assert "Team-game sides audited: 105,058" in report
assert "Point candidates: 662" in report
assert "Minute candidates: 1,211" in report
assert "Unique affected team-game keys: 1,300" in report
assert "Unique affected games: 1,164" in report
assert "Cross-section overlaps: 573" in report
assert (
    "Point causes: 627 incomplete payloads; 27 bad references; "
    "7 phantom rows; 1 isolated discrepancy"
) in report
assert (
    "Minute causes: 573 point-deficit overlaps; 264 active MIN=0 sides; "
    "228 residual-six-to-eight sides; 146 other anomalies"
) in report
assert "Approved point exceptions: 0" in report
assert "Approved minute exceptions: 0" in report
assert "Applied matchup repairs: 0" in report
assert "Applied designation repairs: 10" in report
assert "ade10d5af96f47ba633b8526488eb9cb94607ce024aa0ce7e0b63ed9b05d3388" in report
```

Retain hash/snapshot-prefix mismatch and `restore_verified is True` failure
tests. Add a CLI final-report test that passes the real manifest and verifies
the written file is exactly the value returned by `render_final_audit` for the
same manifest hash, snapshot name, and restore flag.

- [ ] **Step 2: Write failing repository-layout and generated-data tests**

Require repository verification to execute the snapshot scripts' presence and
regular-file checks, require the committed final audit path, and reject any
tracked file under `data/raw` or `data/parquet-large`. Add a test that feeds
`scripts/verify_repository.py` a controlled tracked-file list containing
`data/parquet-large/player_games/_manifest.json` and asserts a nonzero result
identifying that path. Do not add tests that grep human prose.

Use the following checklist for the manual documentation self-review in Step
8; README, data dictionary, and final audit must expose:

- trusted 2012–2026 scope, 37 consumed identities, and 15 partitions;
- separate 1984–2026 raw archive with 93 identities;
- 1984–2011 quarantine, the 1,082,012 / 52,529 / 105,058 source totals,
  the 662 / 1,211 / 1,300 / 1,164 / 573 discrepancy totals, and both exact
  cause partitions from the governing amendment;
- empty point/minute ledger and unchanged strict minute rule;
- zero publication matchup repairs and ten designation repairs;
- fetch, full source-audit, build, archive, restore, and validation commands;
- regular exact-ID and postseason composite coverage joins;
- cache, publication, manifest, and snapshot layouts;
- the no-roster/no-absence warning; and
- no tracked file under `data/raw` or `data/parquet-large`.

The final audit's generated semantics are covered by the renderer behavior test
in Step 1. README and data-dictionary prose are checked against this list by the
implementer and task reviewer, not frozen by change-detector tests.

- [ ] **Step 3: Run and observe RED**

```bash
.venv/bin/python -m pytest \
  tests/test_player_games_build.py::test_render_final_audit_binds_manifest_hash_and_restore \
  tests/test_repository_layout.py \
  tests/test_verify_repository.py \
  -q
```

Expected: failures name missing two-scope audit content and user-facing release
audit/repository artifacts; existing hash/restore controls remain green.

- [ ] **Step 4: Implement deterministic final-audit rendering**

Keep `render_final_audit` bound to the exact canonical manifest SHA, snapshot
prefix, and verified restore. Read publication counts from the recomputed
manifest and render the approved historical evidence as immutable reviewed
facts. Include the full source-audit and candidate SHA-256 values from Global
Constraints. Reject manifests whose scope, source count, partition count,
exception counts, or repair counts differ from `[2012..2026]`, 37, 15, 0/0,
or 0/10 before rendering.

Do not load the git-ignored diagnostic reports at runtime. Their reviewed
summary is committed in the governing amendment and reproduced in the final
audit so later removal of the SDD workspace does not erase the decision.

- [ ] **Step 5: Update README and the data dictionary**

Document exact Arrow columns/nullability/order, primary key, source/cache
identity, the source/publication split, coverage joins, empty-ledger behavior,
repair ledgers, manifest fields, commands, snapshot environment variables,
capacity rule, immutable layout, restore refusal, and quarantine boundary.
Include this exact warning:

> A missing `player_games` row means only that LeagueGameLog did not credit an
> appearance; it does not prove roster membership, eligibility, injury, rest,
> or expected participation.

Do not describe 1984–2011 as published, validated, accepted exceptions, or a
corrected workload.

- [ ] **Step 6: Render the final audit from verified artifacts**

Use the exact manifest and snapshot values printed by Task 5:

```bash
test ! -e /tmp/player-game-final-audit-input
player_manifest_sha256="$(sha256sum data/parquet-large/player_games/_manifest.json | cut -d' ' -f1)"
player_snapshot_name="player-games-archive-2026-${player_manifest_sha256:0:12}"
.venv/bin/python scripts/audit_player_logs.py \
  --data-dir data \
  --output-dir /tmp/player-game-final-audit-input \
  --final-report docs/audits/2026-08-02-player-games-source-audit.md \
  --manifest-sha256 "$player_manifest_sha256" \
  --snapshot-name "$player_snapshot_name" \
  --restore-verified
```

Expected: the command rejects any digest/prefix mismatch and writes the audit
only after independent publication validation succeeds.

- [ ] **Step 7: Run the complete acceptance gate**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/verify_repository.py
git diff --check
git ls-files data/raw data/parquet-large
git status --short --branch
```

Expected: the full suite and repository verification pass; `git diff --check`
is silent; `git ls-files` prints nothing; status lists only intentional tracked
Task 6 documentation/audit/test changes on `feat/player-games-workload`.

- [ ] **Step 8: Check every amendment acceptance criterion against evidence**

Record in the Task 6 implementation report the command/artifact that proves
each of the amendment's eight acceptance criteria. Confirm no alternate source,
historical correction, quality-flag schema, D2/D3 code, absence inference,
candidate ledger, raw response, or large Parquet file entered git.

- [ ] **Step 9: Commit final documentation and stop at the branch boundary**

```bash
git add \
  src/nba_data/player_games_build.py \
  scripts/audit_player_logs.py \
  tests/test_player_games_build.py \
  README.md \
  docs/data-dictionary.md \
  docs/audits/2026-08-02-player-games-source-audit.md \
  tests/test_repository_layout.py \
  scripts/verify_repository.py \
  tests/test_verify_repository.py
git commit -m "docs: record trusted player workload release"
```

Report commit list, full-suite count, repository verification, manifest SHA,
snapshot path, 93/37/15 scope counts, zero point/minute exceptions, 0/10 repair
counts, row/game/player counts, restore proof, and clean status. Do not merge,
push, deploy, delete the SDD workspace/worktree, or begin historical
remediation, D2, or D3.

---

## Plan Acceptance Mapping

| Amendment requirement | Implemented by |
|---|---|
| Explicit 1984–2026 source and 2012–2026 publication scopes | Tasks 1–2 |
| Exact 93-pair source inventory and deterministic full diagnostic audit | Tasks 1–2 |
| Empty point/minute ledger with zero publication discrepancies | Tasks 2–3 |
| Exactly 37 consumed source bindings and 15 partitions | Task 3 |
| Zero publication matchup repairs and ten designation repairs | Tasks 2–3 |
| Nine-key deterministic manifest and independent validation | Task 3 |
| Full 93-pair raw archive plus narrower publication snapshot | Tasks 4–5 |
| Immutable archive, absent-target restore, and independent rebuild | Tasks 4–5 |
| Two-scope documentation and preserved quarantine evidence | Task 6 |
| No historical correction, imputation, tolerance widening, D2, or D3 | Global Constraints and Task 6 |

## Execution Boundary

Implement this plan with `superpowers:subagent-driven-development`: one fresh
implementer per task, one task-scoped reviewer after each task, fix/re-review
loops for every Critical or Important finding, and one broad whole-branch
review after Task 6. Stop on the unmerged branch after verification and review;
integration remains a separate explicit user decision.
