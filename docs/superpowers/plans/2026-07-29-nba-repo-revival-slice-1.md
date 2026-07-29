# NBA Repository Revival — Slice 1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to
> implement this plan task-by-task.

**Goal:** Preserve the irreplaceable local NBA data, extract the authored project
from the committed Windows virtual environment, remove `venv/` from Git history,
and prove that a fresh GitHub clone is small, portable, and ready for the data
pipeline work.

**Architecture:** The current dirty working copy is a read-only source after its
contents are backed up. All restructuring and history rewriting happen in a
no-hardlinks disposable clone. The rewrite is verified locally and bundled to
NAS before a separate approval checkpoint permits a guarded remote force-push.
A new sibling clone—not the old working copy—becomes the Slice 2 workspace.

**Tech stack:** Git, `git-filter-repo`, rsync, SHA-256, Python 3.12, uv,
pytest, Markdown.

**Design:** `docs/superpowers/specs/2026-07-29-nba-repo-revival-covid-hca-design.md`

## Scope and safety boundaries

- This plan implements **Slice 1 only**. It does not fetch NBA data, build
  Parquet tables, or run the COVID analysis.
- The source working copy is
  `/home/riz/dev/nba-predictions`.
- The verified archive root is
  `/mnt/nas/llm/documents/nba-predictions-archive/pre-rewrite-e0f8e3a`.
- The disposable rewrite clone is
  `/tmp/nba-predictions-revival-20260729`.
- The post-push working clone is
  `/home/riz/dev/nba-predictions-revived`.
- Never remove or overwrite any of those paths. If an intended new destination
  already exists, stop and inspect it.
- No command in Tasks 1–5 changes GitHub. Task 6 must stop and obtain explicit
  user approval immediately before the force-push.
- Do not clean, reset, stash, or rewrite the source working copy. Its modified
  prediction CSV and untracked files are preservation inputs.

## Current-state assertions

These values were verified while writing the plan:

- source HEAD:
  `e0f8e3adc12c660c225f4f4dbadf89bdd64ce83f`
- GitHub `master`:
  `0447d06026744f2c3121728993e47507e78388e1`
- GitHub URL:
  `https://github.com/wrislin1/nba-predictions.git`
- source branch is two commits ahead of `origin/master`
- `git-filter-repo` is not currently installed
- `/mnt/nas/llm` is the mounted NFS export; `/mnt/nas` by itself is local disk
- preservation inputs include:
  `nba-stats/`, `venv/static/data/`, `PROJECT_REVIEW.md`,
  `PROJECT_DATA_FINDINGS.md`, the revised design/plan documents, and the dirty
  working-tree patch

If HEAD, the remote URL, or GitHub `master` differs at execution time, stop and
review the new state instead of substituting it silently.

---

### Task 1: Prove the preflight conditions

**Files:**

- Read only:
  `.git/`, `venv/static/data/`, `nba-stats/`, `PROJECT_REVIEW.md`,
  `PROJECT_DATA_FINDINGS.md`
- External tool install:
  user-local `git-filter-repo`

**Step 1: Reconfirm the protected working-tree state**

Run:

```bash
cd /home/riz/dev/nba-predictions
git status --short --branch
git rev-parse HEAD
git remote get-url origin
git ls-remote origin refs/heads/master
```

Expected:

- HEAD equals `e0f8e3adc12c660c225f4f4dbadf89bdd64ce83f`.
- remote URL equals
  `https://github.com/wrislin1/nba-predictions.git`.
- the remote line starts with
  `0447d06026744f2c3121728993e47507e78388e1`.
- status still includes the modified prediction CSV and the untracked data and
  review files.

If any assertion differs, stop. Do not reset the source to make it match.

**Step 2: Prove that the archive path is on NFS and writable**

Run:

```bash
findmnt -T /mnt/nas/llm/documents
test -w /mnt/nas/llm/documents
test ! -e /mnt/nas/llm/documents/nba-predictions-archive/pre-rewrite-e0f8e3a
```

Expected: `findmnt` identifies `/mnt/nas/llm` as NFS, both `test` commands
exit 0, and the archive destination does not already exist.

**Step 3: Install the missing root tool**

Run:

```bash
/home/riz/.local/bin/uv tool install git-filter-repo
git filter-repo --version
```

Expected: the version command succeeds. If installation is blocked, pause and
ask for permission to install the tool; do not substitute a hand-written
`filter-branch` command.

**Step 4: Confirm required local utilities**

Run:

```bash
command -v git
command -v rsync
command -v sha256sum
python3 --version
/home/riz/.local/bin/uv --version
```

Expected: all commands resolve, and Python reports 3.12.x.

---

### Task 2: Create and verify the preservation archive

**Files:**

- Create:
  `/mnt/nas/llm/documents/nba-predictions-archive/pre-rewrite-e0f8e3a/repository.git/`
- Create:
  `/mnt/nas/llm/documents/nba-predictions-archive/pre-rewrite-e0f8e3a/raw/nba-stats/`
- Create:
  `/mnt/nas/llm/documents/nba-predictions-archive/pre-rewrite-e0f8e3a/raw/venv-static-data/`
- Create:
  `/mnt/nas/llm/documents/nba-predictions-archive/pre-rewrite-e0f8e3a/working-tree/`
- Create:
  `/mnt/nas/llm/documents/nba-predictions-archive/pre-rewrite-e0f8e3a/metadata/`
- Create:
  `/mnt/nas/llm/documents/nba-predictions-archive/pre-rewrite-e0f8e3a/SHA256SUMS`

**Step 1: Create the archive directories**

Run:

```bash
NBA_SOURCE=/home/riz/dev/nba-predictions
NBA_ARCHIVE=/mnt/nas/llm/documents/nba-predictions-archive/pre-rewrite-e0f8e3a
mkdir -p "$NBA_ARCHIVE/raw"
mkdir -p "$NBA_ARCHIVE/working-tree"
mkdir -p "$NBA_ARCHIVE/metadata"
```

**Step 2: Preserve all committed history**

Run:

```bash
NBA_SOURCE=/home/riz/dev/nba-predictions
NBA_ARCHIVE=/mnt/nas/llm/documents/nba-predictions-archive/pre-rewrite-e0f8e3a
git clone --mirror --no-hardlinks "$NBA_SOURCE" "$NBA_ARCHIVE/repository.git"
git -C "$NBA_ARCHIVE/repository.git" fsck --full
git -C "$NBA_SOURCE" show-ref > "$NBA_ARCHIVE/metadata/source-refs.txt"
git -C "$NBA_ARCHIVE/repository.git" show-ref > "$NBA_ARCHIVE/metadata/mirror-refs.txt"
cmp "$NBA_ARCHIVE/metadata/source-refs.txt" "$NBA_ARCHIVE/metadata/mirror-refs.txt"
```

Expected: `git fsck` and `cmp` both succeed.

**Step 3: Preserve the irreplaceable data and current documents**

Run:

```bash
NBA_SOURCE=/home/riz/dev/nba-predictions
NBA_ARCHIVE=/mnt/nas/llm/documents/nba-predictions-archive/pre-rewrite-e0f8e3a
rsync -a --info=stats2 "$NBA_SOURCE/nba-stats/" "$NBA_ARCHIVE/raw/nba-stats/"
rsync -a --info=stats2 "$NBA_SOURCE/venv/static/data/" "$NBA_ARCHIVE/raw/venv-static-data/"
rsync -a "$NBA_SOURCE/PROJECT_REVIEW.md" "$NBA_ARCHIVE/working-tree/"
rsync -a "$NBA_SOURCE/PROJECT_DATA_FINDINGS.md" "$NBA_ARCHIVE/working-tree/"
rsync -a "$NBA_SOURCE/requirements.txt" "$NBA_ARCHIVE/working-tree/requirements-utf16-original.txt"
rsync -a "$NBA_SOURCE/docs/" "$NBA_ARCHIVE/working-tree/docs/"
```

Do not omit the currently modified
`venv/static/data/nba_predictions_2022_2023_new.csv`; rsync must read it from
the working tree, not from Git.

**Step 4: Record the exact source and remote state**

Run:

```bash
NBA_SOURCE=/home/riz/dev/nba-predictions
NBA_ARCHIVE=/mnt/nas/llm/documents/nba-predictions-archive/pre-rewrite-e0f8e3a
git -C "$NBA_SOURCE" rev-parse HEAD > "$NBA_ARCHIVE/metadata/source-head.txt"
git -C "$NBA_SOURCE" remote get-url origin > "$NBA_ARCHIVE/metadata/origin-url.txt"
git -C "$NBA_SOURCE" ls-remote origin refs/heads/master > "$NBA_ARCHIVE/metadata/remote-master.txt"
git -C "$NBA_SOURCE" status --porcelain=v2 > "$NBA_ARCHIVE/metadata/source-status.txt"
git -C "$NBA_SOURCE" diff --binary --full-index > "$NBA_ARCHIVE/working-tree/tracked-changes.patch"
```

Use ordinary shell redirection only for these generated evidence files; do not
edit their contents.

**Step 5: Generate and verify content checksums**

From `$NBA_ARCHIVE`, hash the copied raw and working-tree files in sorted
relative-path order:

```bash
cd /mnt/nas/llm/documents/nba-predictions-archive/pre-rewrite-e0f8e3a
find raw working-tree -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > SHA256SUMS
sha256sum -c SHA256SUMS
```

Expected: every entry reports `OK`.

**Step 6: Compare source and archive trees without changing either**

Run:

```bash
NBA_SOURCE=/home/riz/dev/nba-predictions
NBA_ARCHIVE=/mnt/nas/llm/documents/nba-predictions-archive/pre-rewrite-e0f8e3a
rsync -a --checksum --delete --dry-run --itemize-changes "$NBA_SOURCE/nba-stats/" "$NBA_ARCHIVE/raw/nba-stats/"
rsync -a --checksum --delete --dry-run --itemize-changes "$NBA_SOURCE/venv/static/data/" "$NBA_ARCHIVE/raw/venv-static-data/"
git -C "$NBA_ARCHIVE/repository.git" fsck --full
```

Expected: both dry runs print no file changes and `git fsck` succeeds.

**Step 7: Reconfirm that the source was not mutated**

Run:

```bash
NBA_SOURCE=/home/riz/dev/nba-predictions
git -C "$NBA_SOURCE" status --short --branch
git -C "$NBA_SOURCE" rev-parse HEAD
```

Expected: the status matches Task 1 and HEAD remains
`e0f8e3adc12c660c225f4f4dbadf89bdd64ce83f`.

---

### Task 3: Create the disposable clone and failing layout tests

**Files:**

- Create:
  `/tmp/nba-predictions-revival-20260729/`
- Create:
  `/tmp/nba-predictions-revival-20260729/tests/test_repository_layout.py`
- Copy:
  current design, Slice 1 plan, and the two review documents

**Step 1: Clone without shared object hardlinks**

Run:

```bash
NBA_SOURCE=/home/riz/dev/nba-predictions
NBA_STAGE=/tmp/nba-predictions-revival-20260729
test ! -e "$NBA_STAGE"
git clone --no-hardlinks "$NBA_SOURCE" "$NBA_STAGE"
git -C "$NBA_STAGE" fsck --full
```

Expected: the clone succeeds, object integrity is good, and subsequent
destructive operations in `$NBA_STAGE` cannot alter source object files.

**Step 2: Copy the current planning and review documents**

Run:

```bash
NBA_SOURCE=/home/riz/dev/nba-predictions
NBA_STAGE=/tmp/nba-predictions-revival-20260729
rsync -a "$NBA_SOURCE/docs/" "$NBA_STAGE/docs/"
rsync -a "$NBA_SOURCE/PROJECT_REVIEW.md" "$NBA_STAGE/"
rsync -a "$NBA_SOURCE/PROJECT_DATA_FINDINGS.md" "$NBA_STAGE/"
```

**Step 3: Write the repository contract test**

Run:

```bash
mkdir -p /tmp/nba-predictions-revival-20260729/tests
```

Create `tests/test_repository_layout.py` with tests that assert:

```python
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_ARCHIVE_FILES = {
    "archive/dashboard/start.py",
    "archive/dashboard/templates/dashboard.html",
    "archive/notebooks/Data Retrieval and Transformation.ipynb",
    "archive/notebooks/NBA Data Automated.ipynb",
    "archive/notebooks/NBA ML.ipynb",
    "PROJECT_REVIEW.md",
    "PROJECT_DATA_FINDINGS.md",
}

REQUIRED_REQUIREMENTS = {
    "beautifulsoup4",
    "duckdb",
    "jupyter",
    "linearmodels",
    "lxml",
    "matplotlib",
    "nba-api",
    "openpyxl",
    "pandas",
    "pyarrow",
    "pytest",
    "requests",
    "seaborn",
    "statsmodels",
}


def normalized_requirement_name(line: str) -> str:
    return re.split(r"[<=>!~\[]", line, maxsplit=1)[0].lower().replace("_", "-")


def test_generated_environment_is_absent() -> None:
    assert not (ROOT / "venv").exists()


def test_authored_artifacts_are_preserved() -> None:
    missing = [path for path in REQUIRED_ARCHIVE_FILES if not (ROOT / path).is_file()]
    assert missing == []


def test_analysis_first_layout_exists() -> None:
    assert (ROOT / "data/parquet/.gitkeep").is_file()
    assert (ROOT / "src/nba_data/__init__.py").is_file()
    assert (ROOT / "analysis/covid_hca/README.md").is_file()
    assert (ROOT / "scripts/verify_repository.py").is_file()


def test_gitignore_protects_generated_and_raw_state() -> None:
    rules = set((ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())
    assert {".venv/", "venv/", "data/raw/", "nba-stats/", "*.duckdb", ".serena/"} <= rules


def test_requirements_are_utf8_and_pinned() -> None:
    raw = (ROOT / "requirements.txt").read_bytes()
    text = raw.decode("utf-8")
    assert b"\x00" not in raw
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    assert all("==" in line or line.startswith(("-", "--")) for line in lines)
    names = {normalized_requirement_name(line) for line in lines if not line.startswith(("-", "--"))}
    assert REQUIRED_REQUIREMENTS <= names


def test_readme_documents_the_new_contract() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for heading in (
        "## Project status",
        "## Repository layout",
        "## Environment setup",
        "## Data policy",
        "## Revival roadmap",
    ):
        assert heading in readme
```

**Step 4: Run the test and confirm it fails for the old layout**

Run:

```bash
cd /tmp/nba-predictions-revival-20260729
/home/riz/.local/bin/uv run --with pytest pytest -q tests/test_repository_layout.py
```

Expected: failures include the existing `venv/`, missing archive paths, and
the UTF-16 requirements file. A passing test here means the contract is not
testing the old failure mode; strengthen it before proceeding.

---

### Task 4: Build and commit the clean analysis-first tip

**Files:**

- Move:
  `Data Retrieval and Transformation.ipynb` →
  `archive/notebooks/Data Retrieval and Transformation.ipynb`
- Move:
  `NBA Data Automated.ipynb` →
  `archive/notebooks/NBA Data Automated.ipynb`
- Move:
  `NBA ML.ipynb` → `archive/notebooks/NBA ML.ipynb`
- Move:
  `venv/start.py` → `archive/dashboard/start.py`
- Move:
  `venv/templates/` → `archive/dashboard/templates/`
- Move:
  `venv/static/img/` → `archive/dashboard/static/img/`
- Remove in the disposable clone only:
  remaining `venv/`
- Modify:
  `README.md`, `requirements.txt`
- Create:
  `requirements.in`, `.gitignore`, `data/parquet/.gitkeep`,
  `src/nba_data/__init__.py`, `analysis/covid_hca/README.md`,
  `archive/dashboard/README.md`, `archive/notebooks/README.md`,
  `scripts/verify_repository.py`,
  `docs/audits/2026-07-29-pre-rewrite.md`

**Step 1: Move the authored artifacts before removing `venv/`**

Run:

```bash
cd /tmp/nba-predictions-revival-20260729
mkdir -p archive/dashboard/static
mkdir -p archive/notebooks
mkdir -p data/parquet
mkdir -p src/nba_data
mkdir -p analysis/covid_hca
mkdir -p scripts
mkdir -p docs/audits

git mv "Data Retrieval and Transformation.ipynb" archive/notebooks/
git mv "NBA Data Automated.ipynb" archive/notebooks/
git mv "NBA ML.ipynb" archive/notebooks/
git mv venv/start.py archive/dashboard/start.py
git mv venv/templates archive/dashboard/templates
git mv venv/static/img archive/dashboard/static/img
git rm -r venv
```

The last command is allowed only in the disposable clone and only after Task 2
passed. Do not run it in `/home/riz/dev/nba-predictions`.

**Step 2: Add the repository policy files**

Create `.gitignore` with:

```gitignore
.venv/
venv/
__pycache__/
*.py[cod]
.pytest_cache/
.ipynb_checkpoints/
.idea/
.serena/
.DS_Store
desktop.ini

data/raw/
nba-stats/
*.duckdb
```

Create `requirements.in` with:

```text
beautifulsoup4
duckdb
jupyter
linearmodels
lxml
matplotlib
nba_api
openpyxl
pandas
pyarrow
pytest
requests
seaborn
statsmodels
```

Compile a fully pinned UTF-8 requirements file:

```bash
cd /tmp/nba-predictions-revival-20260729
/home/riz/.local/bin/uv pip compile --upgrade --python-version 3.12 --universal requirements.in --output-file requirements.txt
file requirements.txt
```

Expected: `file` reports UTF-8 or ASCII text, not UTF-16.
The explicit `--upgrade` prevents uv from preserving incompatible preferences
from the legacy output file.

Create `src/nba_data/__init__.py` with:

```python
"""Reproducible NBA data ingestion and transformation package."""

__version__ = "0.1.0"
```

Create `data/parquet/.gitkeep`.

**Step 3: Document archived and future-facing areas**

Create `archive/dashboard/README.md` stating:

- the Flask dashboard is historical, not maintained;
- it was extracted from the committed Windows environment;
- its original CSV/model inputs are preserved in the verified NAS archive;
- it is not part of the supported installation or test path.

Create `archive/notebooks/README.md` stating:

- the three notebooks are unchanged capstone artifacts;
- they are preserved for provenance rather than treated as the new pipeline;
- maintained ingestion will live under `src/nba_data/`.

Create `analysis/covid_hca/README.md` stating:

- Slice 3 will contain the reproducible notebook, report, and figures;
- no analysis is claimed until Slice 2 supplies validated 2019-20 through
  2025-26 data;
- regular-season bubble games and attendance semantics are explicit gates.

**Step 4: Rewrite the root README**

Replace the current README with concise sections:

1. `## Project status` — 2022 capstone under staged revival.
2. `## What is preserved` — 40,828 historical games, validated point-in-time
   features, 1,050 roster CSVs, and 1,044 leader workbooks.
3. `## Repository layout` — maintained source/data/analysis versus archive.
4. `## Environment setup` — Python 3.12 `.venv`, install from pinned
   `requirements.txt`, run pytest.
5. `## Data policy` — curated Parquet may be committed; raw responses,
   `nba-stats/`, DuckDB files, and secrets stay untracked; originals live in
   the verified archive.
6. `## Revival roadmap` — Slice 1 repository rescue, Slice 2 ingestion, Slice
   3 COVID HCA analysis.
7. `## Historical caveats` — archived dashboard and models are provenance, not
   validated current predictions.

Link both project-review documents and the approved design.

**Step 5: Add deterministic rewrite verification**

Create `scripts/verify_repository.py` as a standard-library CLI that:

- resolves repository root from the script path;
- runs `git fsck --full`;
- reads `git ls-files` and fails if any tracked path is `venv` or starts
  `venv/`;
- checks every path named by `REQUIRED_ARCHIVE_FILES` in the layout test;
- decodes `requirements.txt` as UTF-8 and fails on NUL bytes;
- verifies `.gitignore` contains the required raw/generated rules;
- sums file sizes below `.git/`;
- accepts `--max-git-mib` as an integer, defaulting to 100;
- prints one actionable error per failed check and returns 1;
- prints the tracked-file count and `.git` size and returns 0 on success.

Do not make it delete, rewrite, fetch, or push anything.

**Step 6: Record the preservation audit**

Create `docs/audits/2026-07-29-pre-rewrite.md` containing:

- the exact source HEAD and GitHub `master` assertions from this plan;
- the dirty/untracked preservation inputs;
- historical inventory figures from `PROJECT_DATA_FINDINGS.md`;
- the exact NAS archive root;
- the commands used to verify the mirror and SHA-256 manifest;
- a statement that the full checksum manifest remains on NAS because raw data
  is intentionally not committed;
- a restore procedure using `git clone` from `repository.git` and rsync from
  `raw/`.

**Step 7: Run the repository contract**

Run:

```bash
cd /tmp/nba-predictions-revival-20260729
/home/riz/.local/bin/uv run --with pytest pytest -q tests/test_repository_layout.py
python3 scripts/verify_repository.py --max-git-mib 600
git diff --check
git status --short
```

Expected:

- pytest passes;
- the verification script passes at the generous pre-rewrite 600 MiB limit;
- `git diff --check` reports no errors;
- status contains only the intended restructuring, policy, audit, test, and
  documentation changes.

**Step 8: Commit the clean tip**

Run:

```bash
cd /tmp/nba-predictions-revival-20260729
git add .gitignore README.md requirements.in requirements.txt
git add PROJECT_REVIEW.md PROJECT_DATA_FINDINGS.md
git add analysis archive data docs scripts src tests
git status --short
git diff --cached --stat
git commit -m "refactor: extract project from committed environment"
```

Expected: commit succeeds. Confirm `git status --short` is empty.

---

### Task 5: Rewrite and verify history locally

**Files:**

- Rewrite only:
  `/tmp/nba-predictions-revival-20260729/.git/`
- Create on NAS:
  `rewritten-before-push.bundle`
- Create on NAS:
  `metadata/rewritten-head.txt`

**Step 1: Create a pre-rewrite recovery bundle**

Run from `$NBA_STAGE`:

```bash
cd /tmp/nba-predictions-revival-20260729
NBA_ARCHIVE=/mnt/nas/llm/documents/nba-predictions-archive/pre-rewrite-e0f8e3a
git bundle create "$NBA_ARCHIVE/staged-before-filter.bundle" --all
git bundle verify "$NBA_ARCHIVE/staged-before-filter.bundle"
```

Expected: bundle verification succeeds.

**Step 2: Remove `venv/` from every revision**

Run:

```bash
cd /tmp/nba-predictions-revival-20260729
git filter-repo --force --path venv/ --invert-paths
git gc --prune=now
```

This is intentionally destructive only to `$NBA_STAGE`. Do not use a source
path, broad path, or environment variable that has not been printed and
checked immediately beforehand.

**Step 3: Verify the rewrite**

Run:

```bash
cd /tmp/nba-predictions-revival-20260729
git fsck --full
git log --all -- venv/
git ls-files
python3 scripts/verify_repository.py --max-git-mib 100
/home/riz/.local/bin/uv run --with pytest pytest -q
git status --short
git count-objects -vH
du -sm .git
```

Expected:

- `git log --all -- venv/` prints nothing;
- no tracked path is under `venv/`;
- all tests pass;
- the working tree is clean;
- `.git` is below 100 MiB.

If the size is still high, inspect `git rev-list --objects --all` and stop.
Do not broaden the filter until the unexpected retained objects are understood.

**Step 4: Preserve the rewritten candidate before remote mutation**

Run:

```bash
cd /tmp/nba-predictions-revival-20260729
NBA_ARCHIVE=/mnt/nas/llm/documents/nba-predictions-archive/pre-rewrite-e0f8e3a
git rev-parse HEAD > "$NBA_ARCHIVE/metadata/rewritten-head.txt"
git bundle create "$NBA_ARCHIVE/rewritten-before-push.bundle" --all
git bundle verify "$NBA_ARCHIVE/rewritten-before-push.bundle"
```

Expected: the HEAD file is nonempty and bundle verification succeeds.

---

### Task 6: Re-establish the remote and stop for force-push approval

**Files:**

- Modify only in the disposable clone:
  `.git/config`
- External mutation after approval:
  GitHub `wrislin1/nba-predictions` branch `master`

**Step 1: Re-add only the recorded GitHub remote**

`git-filter-repo` normally removes `origin`. Read
`$NBA_ARCHIVE/metadata/origin-url.txt`; it must contain exactly:

```text
https://github.com/wrislin1/nba-predictions.git
```

Then run:

```bash
cd /tmp/nba-predictions-revival-20260729
git remote add origin https://github.com/wrislin1/nba-predictions.git
git fetch origin master
git remote -v
git rev-parse refs/remotes/origin/master
```

Expected remote master:
`0447d06026744f2c3121728993e47507e78388e1`.

If it differs, stop. Do not force-push over an unreviewed remote change.

**Step 2: Present the final local evidence and ask for approval**

Report:

- old GitHub master SHA;
- rewritten candidate SHA;
- NAS mirror, raw archive, checksum, and bundle verification results;
- before/after `.git` sizes;
- pytest and `git fsck` results;
- exact command proposed:

```bash
cd /tmp/nba-predictions-revival-20260729
git push --force-with-lease=refs/heads/master:0447d06026744f2c3121728993e47507e78388e1 origin HEAD:refs/heads/master
```

Stop and wait for explicit user approval. Design approval, plan approval, and
“continue” from an earlier checkpoint do not satisfy this gate.

**Step 3: Push only after explicit approval**

After approval, run exactly:

```bash
cd /tmp/nba-predictions-revival-20260729
git push --force-with-lease=refs/heads/master:0447d06026744f2c3121728993e47507e78388e1 origin HEAD:refs/heads/master
```

If GitHub rejects the push, stop and report the branch-protection or lease
error. Do not bypass it.

**Step 4: Verify the remote points to the rewritten candidate**

Run:

```bash
cd /tmp/nba-predictions-revival-20260729
git rev-parse HEAD
git ls-remote origin refs/heads/master
```

Expected: both commands report the same rewritten SHA.

---

### Task 7: Verify a genuinely fresh clone

**Files:**

- Create:
  `/home/riz/dev/nba-predictions-revived/`
- Create, untracked:
  `/home/riz/dev/nba-predictions-revived/.venv/`

**Step 1: Clone GitHub into a new sibling directory**

Run:

```bash
test ! -e /home/riz/dev/nba-predictions-revived
git clone https://github.com/wrislin1/nba-predictions.git /home/riz/dev/nba-predictions-revived
git -C /home/riz/dev/nba-predictions-revived fsck --full
```

Do not rename or delete the old source clone.

**Step 2: Prove the fresh repository contract**

Run:

```bash
cd /home/riz/dev/nba-predictions-revived
python3 scripts/verify_repository.py --max-git-mib 100
git status --short
du -sm .git
```

Expected: verification passes, status is clean, and `.git` is below 100 MiB.

**Step 3: Create and test the supported Linux environment**

Run:

```bash
cd /home/riz/dev/nba-predictions-revived
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest -q
```

Expected: installation and all tests succeed without importing or executing
the archived Flask dashboard.

**Step 4: Final evidence**

Run:

```bash
cd /home/riz/dev/nba-predictions-revived
git status --short
git rev-parse HEAD
git ls-remote origin refs/heads/master
python3 scripts/verify_repository.py --max-git-mib 100
```

Expected:

- working tree is clean because `.venv/` is ignored;
- local and remote SHAs agree;
- verification passes.

## Slice 1 completion criteria

Slice 1 is complete only when all of the following are true:

1. The NAS mirror passes `git fsck`.
2. Every archived raw/working-tree file passes `sha256sum -c`.
3. Checksum-aware rsync dry runs report no source/archive differences.
4. Both review documents and all authored notebooks/dashboard files exist in
   the rewritten repository.
5. No revision or tracked path contains `venv/`.
6. The rewritten `.git` directory and a fresh GitHub clone are each below
   100 MiB.
7. The pinned requirements install in a Linux Python 3.12 `.venv`.
8. Pytest and `scripts/verify_repository.py` pass in the fresh clone.
9. The old dirty source clone remains untouched and is clearly treated as
   archive-only.

After these checks pass, stop. Write the Slice 2 ingestion plan only after
running the live one-season `nba_api` and basketball-reference source/schema
spikes required by the design.
