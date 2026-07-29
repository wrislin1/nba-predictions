# Pre-rewrite preservation audit

Date: 2026-07-29

## Recorded Git state

- Source working copy: `/home/riz/dev/nba-predictions`
- Source branch: `master`, two commits ahead of `origin/master`
- Source HEAD: `e0f8e3adc12c660c225f4f4dbadf89bdd64ce83f`
- GitHub `master`: `0447d06026744f2c3121728993e47507e78388e1`
- Origin: `https://github.com/wrislin1/nba-predictions.git`

The source contained preservation inputs that were intentionally neither
cleaned nor reset:

- modified
  `venv/static/data/nba_predictions_2022_2023_new.csv`;
- modified revival design;
- untracked `nba-stats/`;
- untracked `PROJECT_REVIEW.md` and `PROJECT_DATA_FINDINGS.md`;
- untracked Slice 1 plan; and
- tool-local `.serena/` state.

The complete `git status --porcelain=v2`, tracked binary patch, source refs,
origin URL, and remote branch observation are stored with the archive
metadata.

## Historical data inventory

The local `nba-stats/` collection contains 4,288 files totaling 73,322,885
bytes: 3,243 CSV files, 1,044 Excel workbooks, and one `desktop.ini`. All CSV
and Excel files opened successfully during the data audit.

The canonical historical game collection covers 1983-84 through 2018-19 and
contains 40,828 games represented by 81,656 team-game rows. The point-in-time
cumulative features were recomputed and validated without mismatches. The
collection also contains 1,050 roster CSVs and 1,044 team-season leader
workbooks.

## Archive location and verification

The verified archive root is:

```text
/mnt/nas/llm/documents/nba-predictions-archive/pre-rewrite-e0f8e3a
```

The committed history mirror was checked with:

```bash
git -C repository.git fsck --full
cmp metadata/source-refs.txt metadata/mirror-refs.txt
```

Raw and working-tree files were hashed in sorted relative-path order and
verified with:

```bash
find raw working-tree -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > SHA256SUMS
sha256sum -c SHA256SUMS
```

Source/archive equality was checked without mutation using:

```bash
rsync -a --checksum --delete --dry-run --itemize-changes \
  /home/riz/dev/nba-predictions/nba-stats/ raw/nba-stats/
rsync -a --checksum --delete --dry-run --itemize-changes \
  /home/riz/dev/nba-predictions/venv/static/data/ raw/venv-static-data/
```

The full checksum manifest remains on NAS because raw data is intentionally
not committed to Git.

## Restore procedure

Restore the recorded repository to a new destination:

```bash
git clone \
  /mnt/nas/llm/documents/nba-predictions-archive/pre-rewrite-e0f8e3a/repository.git \
  /path/to/restored-nba-predictions
```

Restore the raw datasets without overwriting an existing destination:

```bash
rsync -a \
  /mnt/nas/llm/documents/nba-predictions-archive/pre-rewrite-e0f8e3a/raw/nba-stats/ \
  /path/to/restored-nba-predictions/nba-stats/
rsync -a \
  /mnt/nas/llm/documents/nba-predictions-archive/pre-rewrite-e0f8e3a/raw/venv-static-data/ \
  /path/to/restored-dashboard-data/
```

Before using restored files, run `sha256sum -c SHA256SUMS` from the archive
root and confirm the destination paths are new or intentionally selected.
