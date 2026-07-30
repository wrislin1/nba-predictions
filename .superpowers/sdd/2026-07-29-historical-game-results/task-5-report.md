# Task 5 — Historical frame assembly and publication gates

## Status

Implemented and reviewed. Publication assembly now combines cache-verified
season rows, season-aware franchise lineage, documented site corrections, and
an exact public schema before running every validation gate.

## TDD evidence

- RED: `.venv/bin/python -m pytest tests/test_historical_build.py -v`
  initially produced 14 expected failures: `assemble`, `validate`, and the
  publication-schema constants did not exist.
- GREEN: after implementation, the same focused suite passed (29 tests).
- Review regression RED: malformed incomplete, string-dtype, and
  duplicate-column frames each initially leaked pandas exceptions rather than
  `HistoricalValidationError`.
- GREEN: their focused regression run passed; the final historical-build suite
  reports `32 passed in 0.36s`.

## Validation gates and deterministic report

`validate` accumulates diagnostics for count/schema/season-set and span,
league-era, identity/null, team/franchise, score/margin, overtime evidence,
attendance, site-status, and URL gates before raising one
`HistoricalValidationError`. Parser-only overtime evidence stays outside the
published frame.

Successful reports contain sorted per-season game totals and site counts,
literal-float boxscore/venue/raw-attendance coverage (with no minimum
coverage threshold), and stable CSV SHA-256 digests for each season.

## Verification commands

- `.venv/bin/python -m pytest tests/test_historical_build.py -q` —
  `32 passed in 0.36s`
- `.venv/bin/python -m pytest tests/test_franchises.py tests/test_historical_bref.py -q`
  — `43 passed in 2.49s` (pre-final focused compatibility run)
- `.venv/bin/python -m pytest -q` — exited 0 on the final full-suite run.
- `git diff --check` — clean.
- `.venv/bin/python -m compileall -q src/nba_data` — exited 0.

## Files

- `src/nba_data/historical_build.py`
- `tests/test_historical_build.py`

## Review

An independent review found and verified the fix for malformed-schema paths
that could leak pandas exceptions. The follow-up review reported no remaining
important findings.

## Concerns

No open implementation concerns. Serena reported only its environment-level
missing-pandas import-resolution diagnostic; runtime tests use the project
virtual environment successfully.

## Commit

`958eadb feat: assemble and validate historical games`
