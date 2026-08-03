#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C
umask 077

root="$(cd "$(dirname "$0")/.." && pwd)"
NBA_PLAYER_GAMES_ARCHIVE_ROOT=${NBA_PLAYER_GAMES_ARCHIVE_ROOT:-/mnt/nas/llm/documents/nba-predictions-archive}
NBA_PLAYER_GAMES_DATA_DIR=${NBA_PLAYER_GAMES_DATA_DIR:-data}
NBA_PLAYER_GAMES_REFERENCE_DIR=${NBA_PLAYER_GAMES_REFERENCE_DIR:-${NBA_PLAYER_GAMES_DATA_DIR}/parquet}
archive_root="$NBA_PLAYER_GAMES_ARCHIVE_ROOT"
data_dir="$NBA_PLAYER_GAMES_DATA_DIR"
reference_dir="$NBA_PLAYER_GAMES_REFERENCE_DIR"
raw_destination="$data_dir/raw/stats_nba/player_logs"
publication_destination="$data_dir/parquet-large/player_games"

export PYTHONPATH="$root/src${PYTHONPATH:+:$PYTHONPATH}"
python_bin="$root/.venv/bin/python"

test "$#" -eq 1 || {
  printf 'usage: %s <snapshot-name>\n' "$0" >&2
  exit 2
}
snapshot_name="$1"

if [[ ! "$snapshot_name" =~ ^player-games-archive-2026-([0-9a-f]{12})$ ]]; then
  printf 'invalid snapshot name (expected player-games-archive-2026-<12 hex>): %s\n' \
    "$snapshot_name" >&2
  exit 1
fi
name_sha12="${BASH_REMATCH[1]}"

require_exact_snapshot_layout() {
  local dir="$1"
  local entries expected
  entries="$(cd "$dir" && find . -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort)"
  expected="$(printf 'SHA256SUMS\nparquet-large\nraw\n' | LC_ALL=C sort)"
  if [ "$entries" != "$expected" ]; then
    printf 'unexpected snapshot top-level entries in %s:\n%s\n' "$dir" "$entries" >&2
    return 1
  fi
  test -d "$dir/raw/stats_nba/player_logs" && test ! -L "$dir/raw/stats_nba/player_logs" || {
    printf 'missing or unsafe raw/stats_nba/player_logs under %s\n' "$dir" >&2
    return 1
  }
  test -d "$dir/parquet-large/player_games" && test ! -L "$dir/parquet-large/player_games" || {
    printf 'missing or unsafe parquet-large/player_games under %s\n' "$dir" >&2
    return 1
  }
  test -f "$dir/SHA256SUMS" && test ! -L "$dir/SHA256SUMS" || {
    printf 'missing or unsafe SHA256SUMS under %s\n' "$dir" >&2
    return 1
  }
  test "$(cd "$dir/raw" && find . -mindepth 1 -maxdepth 1 -printf '%f\n')" = "stats_nba" || {
    printf 'unexpected entries under %s/raw\n' "$dir" >&2
    return 1
  }
  test "$(cd "$dir/raw/stats_nba" && find . -mindepth 1 -maxdepth 1 -printf '%f\n')" = "player_logs" || {
    printf 'unexpected entries under %s/raw/stats_nba\n' "$dir" >&2
    return 1
  }
  test "$(cd "$dir/parquet-large" && find . -mindepth 1 -maxdepth 1 -printf '%f\n')" = "player_games" || {
    printf 'unexpected entries under %s/parquet-large\n' "$dir" >&2
    return 1
  }
}

# Parse and validate SHA256SUMS *before* any checksum is trusted: exact
# grammar, no unsafe path components, no duplicates, and its entry set must
# equal the lstat-derived regular-file inventory (excluding SHA256SUMS
# itself). Only after this passes do we run sha256sum --check.
validate_sha256sums_grammar() {
  local dir="$1"
  local sums="$dir/SHA256SUMS"
  local inventory listed line_count path part bad
  local -a parts
  # Membership/dedup uses a bash associative array (O(1) per line) instead
  # of re-scanning an ever-growing string with a forked grep per line: the
  # latter is quadratic and, at real archive scale (tens of thousands of
  # files), turns this offline check into a multi-minute hang on the sole
  # recovery path. Grammar matching uses bash's own `=~` (no fork) for the
  # same reason.
  local -A seen=()

  inventory="$(cd "$dir" && find . -type f ! -name SHA256SUMS -printf '%P\n' | LC_ALL=C sort)"

  line_count=0
  while IFS= read -r line || [ -n "$line" ]; do
    line_count=$((line_count + 1))
    if [[ ! "$line" =~ ^[0-9a-f]{64}\ \ [^[:space:]] ]]; then
      printf 'malformed SHA256SUMS line %d: %s\n' "$line_count" "$line" >&2
      return 1
    fi
    path="${line:66}"
    case "$path" in
      /*)
        printf 'unsafe absolute SHA256SUMS path at line %d: %s\n' "$line_count" "$path" >&2
        return 1
        ;;
    esac
    case "$path" in
      *'\'*)
        printf 'unsafe SHA256SUMS path (backslash) at line %d: %s\n' "$line_count" "$path" >&2
        return 1
        ;;
    esac
    bad=0
    IFS='/' read -r -a parts <<< "$path"
    for part in "${parts[@]}"; do
      case "$part" in
        ""|"."|"..")
          bad=1
          ;;
      esac
    done
    if [ "$bad" -eq 1 ]; then
      printf 'unsafe SHA256SUMS path component at line %d: %s\n' "$line_count" "$path" >&2
      return 1
    fi
    if [ -n "${seen[$path]+x}" ]; then
      printf 'duplicate SHA256SUMS entry: %s\n' "$path" >&2
      return 1
    fi
    seen[$path]=1
  done < "$sums"

  listed="$(printf '%s\n' "${!seen[@]}" | LC_ALL=C sort)"
  if [ "$listed" != "$inventory" ]; then
    printf 'SHA256SUMS entries do not match the on-disk inventory\n' >&2
    return 1
  fi
}

raw_stage=""
raw_stage_dev=""
raw_stage_ino=""
raw_stage_owned=0
publication_stage=""
publication_stage_dev=""
publication_stage_ino=""
publication_stage_owned=0

# Cleanup removes only the exact real directory this process itself
# created, matched by the device/inode identity captured right after
# `mktemp` returned it -- never anything merely matching the staging
# name pattern, which could belong to a different, unrelated run.
remove_owned_stage() {
  local path="$1" expected_dev="$2" expected_ino="$3" current
  if [ -z "$path" ] || [ -L "$path" ] || [ ! -d "$path" ]; then
    return 0
  fi
  current="$(stat -c '%d %i' "$path" 2>/dev/null)" || return 0
  if [ "$current" = "$expected_dev $expected_ino" ]; then
    rm -rf -- "$path"
  fi
}

cleanup() {
  if [ "$raw_stage_owned" -eq 1 ]; then
    remove_owned_stage "$raw_stage" "$raw_stage_dev" "$raw_stage_ino"
  fi
  if [ "$publication_stage_owned" -eq 1 ]; then
    remove_owned_stage "$publication_stage" "$publication_stage_dev" "$publication_stage_ino"
  fi
}
# A signal trap that does not itself call exit does not stop the script --
# bash runs the trap body and then RESUMES execution at the interrupted
# point. Registering `cleanup` directly against INT/TERM (as a combined
# `trap cleanup INT TERM EXIT`) would run cleanup and then let the restore
# continue to completion, silently ignoring a cancellation attempt (and
# having already deleted staging the still-running script needs). The EXIT
# trap and the INT/TERM traps are therefore kept separate: INT/TERM only
# ever `exit 1`, and that explicit exit is what triggers the EXIT trap's
# `cleanup` exactly once.
trap cleanup EXIT
trap 'exit 1' INT TERM

test ! -L "$archive_root" && test -d "$archive_root" || {
  printf 'missing or unsafe player-games archive root: %s\n' "$archive_root" >&2
  exit 1
}
archive_root="$(cd "$archive_root" && pwd -P)"

snapshot_candidate="$archive_root/$snapshot_name"
test ! -L "$snapshot_candidate" && test -d "$snapshot_candidate" || {
  printf 'missing or unsafe snapshot directory: %s\n' "$snapshot_candidate" >&2
  exit 1
}
snapshot="$(cd "$snapshot_candidate" && pwd -P)"

require_exact_snapshot_layout "$snapshot"
validate_sha256sums_grammar "$snapshot"
(cd "$snapshot" && sha256sum --check --strict SHA256SUMS >/dev/null)

manifest_sha256="$(
  sha256sum "$snapshot/parquet-large/player_games/_manifest.json" | cut -d' ' -f1
)"
if [ "${manifest_sha256:0:12}" != "$name_sha12" ]; then
  printf 'snapshot name does not match its own manifest digest: %s\n' "$snapshot" >&2
  exit 1
fi

# Validate SHA256SUMS and semantic layout before any copy is attempted.
"$python_bin" -m nba_data.player_games_snapshot validate-layout \
  --raw-root "$snapshot/raw/stats_nba/player_logs" \
  --publication-root "$snapshot/parquet-large/player_games" \
  --reference-dir "$reference_dir" \
  --manifest-sha256 "$manifest_sha256"

for destination in "$raw_destination" "$publication_destination"; do
  if [ -e "$destination" ] || [ -L "$destination" ]; then
    printf 'refusing to restore over an existing destination: %s\n' "$destination" >&2
    exit 1
  fi
done

raw_parent="$(dirname -- "$raw_destination")"
publication_parent="$(dirname -- "$publication_destination")"
mkdir -p -- "$raw_parent" "$publication_parent"
for parent in "$raw_parent" "$publication_parent"; do
  test -d "$parent" && test ! -L "$parent" || {
    printf 'missing or unsafe restore destination parent: %s\n' "$parent" >&2
    exit 1
  }
done

raw_stage="$(mktemp -d "$raw_parent/.player-games-restore-raw.XXXXXX")"
read -r raw_stage_dev raw_stage_ino < <(stat -c '%d %i' "$raw_stage")
raw_stage_owned=1
publication_stage="$(mktemp -d "$publication_parent/.player-games-restore-publication.XXXXXX")"
read -r publication_stage_dev publication_stage_ino < <(stat -c '%d %i' "$publication_stage")
publication_stage_owned=1

rsync -a --no-times --checksum "$snapshot/raw/stats_nba/player_logs/" "$raw_stage/"
rsync -a --no-times --checksum "$snapshot/parquet-large/player_games/" "$publication_stage/"

# Revalidate the snapshot -- not the stage -- after the copy above, to
# detect any mutation of the archived snapshot that happened concurrently
# with the rsync copy.
(cd "$snapshot" && sha256sum --check --strict SHA256SUMS >/dev/null)
"$python_bin" -m nba_data.player_games_snapshot validate-layout \
  --raw-root "$snapshot/raw/stats_nba/player_logs" \
  --publication-root "$snapshot/parquet-large/player_games" \
  --reference-dir "$reference_dir" \
  --manifest-sha256 "$manifest_sha256"

# Independently prove the staged raw archive can rebuild the staged
# publication byte-for-byte before either is ever published.
"$python_bin" -m nba_data.player_games_snapshot verify-rebuild \
  --raw-root "$raw_stage" \
  --publication-root "$publication_stage" \
  --reference-dir "$reference_dir"
printf 'independent rebuild: ok\n'

# Publish the two destinations with an explicit two-step rollback state
# machine: if the second rename fails, the first destination is renamed
# back to its original staging name so neither final destination remains.
# If that rollback itself fails, every retained path is printed and left
# untouched -- ambiguous recovery data is never deleted.
raw_stage_original="$raw_stage"
raw_stage_original_dev="$raw_stage_dev"
raw_stage_original_ino="$raw_stage_ino"

if [ -e "$raw_destination" ] || [ -L "$raw_destination" ]; then
  printf 'raw destination appeared concurrently: %s\n' "$raw_destination" >&2
  exit 1
fi
mv -T -n "$raw_stage" "$raw_destination" || true
if [ -d "$raw_stage" ]; then
  printf 'refusing to overwrite raw destination (concurrent creation): %s\n' \
    "$raw_destination" >&2
  exit 1
fi
raw_stage_owned=0

# Every way the second half of the publish can fail -- the destination
# appearing concurrently, the post-publish integrity probe on the raw
# destination failing (ENOENT/EACCES/a stalled mount/anything), or the
# rename itself failing or silently no-op'ing -- funnels into the exact
# same rollback attempt below. Nothing in this span may abort the script
# on its own: an unguarded failure here would leave the raw destination
# published, the publication destination unpublished, and no rollback
# attempted at all, which is precisely the half-published state this
# state machine exists to prevent.
second_step_ok=1
if [ -e "$publication_destination" ] || [ -L "$publication_destination" ]; then
  second_step_ok=0
elif ! du -sB1 "$raw_destination" >/dev/null; then
  second_step_ok=0
else
  mv -T -n "$publication_stage" "$publication_destination" || true
  if [ -d "$publication_stage" ]; then
    second_step_ok=0
  fi
fi

if [ "$second_step_ok" -ne 1 ]; then
  if mv -T -n "$raw_destination" "$raw_stage_original" \
    && [ ! -e "$raw_destination" ] \
    && [ -d "$raw_stage_original" ]
  then
    raw_stage="$raw_stage_original"
    raw_stage_dev="$raw_stage_original_dev"
    raw_stage_ino="$raw_stage_original_ino"
    raw_stage_owned=1
    printf 'second-destination publish failed; rolled back raw destination: %s\n' \
      "$raw_destination" >&2
    exit 1
  fi
  raw_stage_owned=0
  publication_stage_owned=0
  {
    printf 'FATAL: restore rollback failed after the raw destination was published.\n'
    printf 'Every retained path below requires manual operator review; nothing was deleted.\n'
    printf '  raw destination (state unknown, do not delete): %s\n' "$raw_destination"
    printf '  raw rollback target (state unknown, do not delete): %s\n' "$raw_stage_original"
    printf '  publication destination (state unknown, do not delete): %s\n' "$publication_destination"
    printf '  publication staging (state unknown, do not delete): %s\n' "$publication_stage"
  } >&2
  exit 1
fi
publication_stage_owned=0

printf 'verified player-games restore: raw=%s publication=%s\n' \
  "$raw_destination" "$publication_destination"
