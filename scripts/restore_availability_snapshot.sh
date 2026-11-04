#!/usr/bin/env bash
# Restore one immutable availability snapshot into
# $NBA_AVAILABILITY_DATA_DIR: the four raw trees and
# parquet-large/availability, each only into an absent destination. The
# player_games publication is a prerequisite, not part of the snapshot:
# the restore refuses unless the live
# ${NBA_AVAILABILITY_DATA_DIR}/parquet-large/player_games/_manifest.json
# hashes to the digest the snapshot's manifest binds under
# references.player_games_manifest.sha256 (recovery step 2, the D1 restore,
# precedes this step 3).
set -euo pipefail
export LC_ALL=C
umask 077

root="$(cd "$(dirname "$0")/.." && pwd)"
NBA_AVAILABILITY_ARCHIVE_ROOT=${NBA_AVAILABILITY_ARCHIVE_ROOT:-/mnt/nas/llm/documents/nba-predictions-archive}
NBA_AVAILABILITY_DATA_DIR=${NBA_AVAILABILITY_DATA_DIR:-data}
archive_root="$NBA_AVAILABILITY_ARCHIVE_ROOT"
data_dir="$NBA_AVAILABILITY_DATA_DIR"
player_games_root="$data_dir/parquet-large/player_games"
player_games_manifest="$player_games_root/_manifest.json"

# The five restored trees, relative to both the snapshot root and $data_dir,
# published in this order (raw trees first, the publication last).
snapshot_trees=(
  raw/stats_nba/box_rosters
  raw/nba_injury_reports
  raw/sportsdataverse/nba_stats_injury_report
  raw/nba_gamebooks
  parquet-large/availability
)
stage_labels=(box-rosters injury-reports derivative gamebooks availability)

export PYTHONPATH="$root/src${PYTHONPATH:+:$PYTHONPATH}"
python_bin="$root/.venv/bin/python"

test "$#" -eq 1 || {
  printf 'usage: %s <snapshot-name>\n' "$0" >&2
  exit 2
}
snapshot_name="$1"

if [[ ! "$snapshot_name" =~ ^availability-archive-2026-([0-9a-f]{12})$ ]]; then
  printf 'invalid snapshot name (expected availability-archive-2026-<12 hex>): %s\n' \
    "$snapshot_name" >&2
  exit 1
fi
name_sha12="${BASH_REMATCH[1]}"

require_regular_tree() {
  # Reject any lstat-visible entry that is neither a regular file nor a
  # directory (symlink, FIFO, socket, device, or otherwise unsafe/stale).
  # `find` never follows symlinks, so this also rejects a symlinked
  # wrapper directory anywhere inside the snapshot -- which `test -d`,
  # rsync, and the checksum verification would all silently follow while
  # the SHA256SUMS inventory covered none of the linked tree.
  local tree_root="$1"
  local bad
  bad="$(find "$tree_root" -mindepth 0 ! -type f ! -type d -print -quit)"
  if [ -n "$bad" ]; then
    printf 'unsafe non-regular entry under %s: %s\n' "$tree_root" "$bad" >&2
    return 1
  fi
}

require_exact_entries() {
  # $1 directory, $2.. the exact expected entry names.
  local dir="$1"
  shift
  local entries expected
  entries="$(cd "$dir" && find . -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort)"
  expected="$(printf '%s\n' "$@" | LC_ALL=C sort)"
  if [ "$entries" != "$expected" ]; then
    printf 'unexpected entries in %s:\n%s\n' "$dir" "$entries" >&2
    return 1
  fi
}

require_exact_snapshot_layout() {
  local dir="$1"
  local component
  require_exact_entries "$dir" SHA256SUMS parquet-large raw
  # Every wrapper component must itself be a real directory: a symlinked
  # `raw` (or any wrapper below it) would be followed by `test -d` and by
  # rsync while `find`'s lstat walk never descends into it, so the
  # SHA256SUMS inventory would silently cover none of the linked tree.
  for component in \
    "$dir/raw" "$dir/raw/stats_nba" "$dir/raw/sportsdataverse" "$dir/parquet-large" \
    "$dir/raw/stats_nba/box_rosters" "$dir/raw/nba_injury_reports" \
    "$dir/raw/sportsdataverse/nba_stats_injury_report" "$dir/raw/nba_gamebooks" \
    "$dir/parquet-large/availability"
  do
    test -d "$component" && test ! -L "$component" || {
      printf 'missing or unsafe snapshot component: %s\n' "$component" >&2
      return 1
    }
  done
  test -f "$dir/SHA256SUMS" && test ! -L "$dir/SHA256SUMS" || {
    printf 'missing or unsafe SHA256SUMS under %s\n' "$dir" >&2
    return 1
  }
  require_exact_entries "$dir/raw" nba_gamebooks nba_injury_reports sportsdataverse stats_nba
  require_exact_entries "$dir/raw/stats_nba" box_rosters
  require_exact_entries "$dir/raw/sportsdataverse" nba_stats_injury_report
  require_exact_entries "$dir/parquet-large" availability
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
  # Membership/dedup uses a bash associative array (O(1) per line) and
  # bash's own `=~` (no fork per line): at real archive scale (hundreds of
  # thousands of files) anything quadratic or forking per line turns this
  # offline check into a multi-minute hang on the sole recovery path.
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

validate_layout() {
  # $1 raw root, $2 publication root, then optional extra arguments.
  local raw="$1" publication="$2"
  shift 2
  "$python_bin" -m nba_data.availability_snapshot validate-layout \
    --raw-root "$raw" \
    --publication-root "$publication" \
    --player-games-root "$player_games_root" \
    "$@"
}

destinations=()
sources=()
stages=("" "" "" "" "")
stage_devs=("" "" "" "" "")
stage_inos=("" "" "" "" "")
stage_owned=(0 0 0 0 0)
# `publish_index` is the highest tree index whose rename MAY have happened;
# it is raised before each `mv`, so no instruction gap exists in which a
# signal could see a published destination that the rollback does not know
# about. A still-present staging directory means that rename never happened.
publish_index=-1
publish_active=0

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

# Un-publish every destination renamed so far, highest index first, by
# renaming each back to its original staging name. A rolled-back stage is
# owned again (so the EXIT cleanup removes it). Returns non-zero if any
# single rollback failed; the caller then reports every retained path.
rollback_published() {
  local i ok=1
  for ((i = publish_index; i >= 0; i--)); do
    if [ -d "${stages[i]}" ] && [ ! -L "${stages[i]}" ]; then
      # The staging directory is still in place: this rename never happened.
      continue
    fi
    if mv -T -n "${destinations[i]}" "${stages[i]}" \
      && [ ! -e "${destinations[i]}" ] \
      && [ -d "${stages[i]}" ]
    then
      stage_owned[i]=1
    else
      ok=0
    fi
  done
  publish_index=-1
  publish_active=0
  [ "$ok" -eq 1 ]
}

report_fatal_rollback() {
  local i
  publish_active=0
  for ((i = 0; i < ${#snapshot_trees[@]}; i++)); do
    stage_owned[i]=0
  done
  {
    printf 'FATAL: restore rollback failed after a destination was published.\n'
    printf 'Every retained path below requires manual operator review; nothing was deleted.\n'
    for ((i = 0; i < ${#snapshot_trees[@]}; i++)); do
      printf '  destination (state unknown, do not delete): %s\n' "${destinations[i]}"
      printf '  staging (state unknown, do not delete): %s\n' "${stages[i]}"
    done
  } >&2
}

cleanup() {
  local i
  if [ "$publish_active" -eq 1 ]; then
    # A signal (or any other forced exit) landed while the destinations were
    # being published: finish the exact rollback the normal failure path
    # performs, so no interruption can leave a half-published state behind.
    if rollback_published; then
      printf 'restore interrupted during publish; rolled back every published destination\n' >&2
    else
      report_fatal_rollback
    fi
  fi
  for ((i = 0; i < ${#snapshot_trees[@]}; i++)); do
    if [ "${stage_owned[i]}" -eq 1 ]; then
      remove_owned_stage "${stages[i]}" "${stage_devs[i]}" "${stage_inos[i]}"
    fi
  done
}
# A signal trap that does not itself call exit does not stop the script --
# bash runs the trap body and then RESUMES execution at the interrupted
# point. Registering `cleanup` directly against INT/TERM would run cleanup
# and then let the restore continue to completion, silently ignoring a
# cancellation attempt (and having already deleted staging the still-running
# script needs). The EXIT trap and the INT/TERM traps are therefore kept
# separate: INT/TERM only ever `exit 1`, and that explicit exit is what
# triggers the EXIT trap's `cleanup` exactly once.
trap cleanup EXIT
trap 'exit 1' INT TERM

test ! -L "$archive_root" && test -d "$archive_root" || {
  printf 'missing or unsafe availability archive root: %s\n' "$archive_root" >&2
  exit 1
}
archive_root="$(cd "$archive_root" && pwd -P)"

snapshot_candidate="$archive_root/$snapshot_name"
test ! -L "$snapshot_candidate" && test -d "$snapshot_candidate" || {
  printf 'missing or unsafe snapshot directory: %s\n' "$snapshot_candidate" >&2
  exit 1
}
snapshot="$(cd "$snapshot_candidate" && pwd -P)"

for tree in "${snapshot_trees[@]}"; do
  destinations+=("$data_dir/$tree")
  sources+=("$snapshot/$tree")
done

# Cheap refusals first: every destination must be absent (never a symlink
# either), and the D1 publication this snapshot was bound to must already be
# in place; both are rechecked after the expensive validation below.
for destination in "${destinations[@]}"; do
  if [ -e "$destination" ] || [ -L "$destination" ]; then
    printf 'refusing to restore over an existing destination: %s\n' "$destination" >&2
    exit 1
  fi
done
test -f "$player_games_manifest" && test ! -L "$player_games_manifest" || {
  printf 'missing player_games manifest (restore the D1 snapshot first): %s\n' \
    "$player_games_manifest" >&2
  exit 1
}

require_exact_snapshot_layout "$snapshot"
require_regular_tree "$snapshot"
validate_sha256sums_grammar "$snapshot"
(cd "$snapshot" && sha256sum --check --strict SHA256SUMS >/dev/null)

manifest_sha256="$(
  sha256sum "$snapshot/parquet-large/availability/_manifest.json" | cut -d' ' -f1
)"
if [ "${manifest_sha256:0:12}" != "$name_sha12" ]; then
  printf 'snapshot name does not match its own manifest digest: %s\n' "$snapshot" >&2
  exit 1
fi

# The snapshot's manifest binds the exact player_games publication it was
# built from; the live one must hash to that digest or nothing is restored.
bound_player_games_sha256="$(
  "$python_bin" -c '
import json, sys
with open(sys.argv[1], "rb") as stream:
    manifest = json.loads(stream.read().decode("utf-8"))
print(manifest["references"]["player_games_manifest"]["sha256"])
' "$snapshot/parquet-large/availability/_manifest.json"
)"
if [[ ! "$bound_player_games_sha256" =~ ^[0-9a-f]{64}$ ]]; then
  printf 'snapshot manifest binds no valid player_games manifest digest: %s\n' "$snapshot" >&2
  exit 1
fi
live_player_games_sha256="$(sha256sum "$player_games_manifest" | cut -d' ' -f1)"
if [ "$live_player_games_sha256" != "$bound_player_games_sha256" ]; then
  printf 'live player_games manifest %s hashes to %s, snapshot binds %s (restore the D1 snapshot named in the manifest first)\n' \
    "$player_games_manifest" "$live_player_games_sha256" "$bound_player_games_sha256" >&2
  exit 1
fi

# Validate the snapshot semantically before any copy is attempted.
validate_layout "$snapshot/raw" "$snapshot/parquet-large/availability" \
  --manifest-sha256 "$manifest_sha256"

for destination in "${destinations[@]}"; do
  if [ -e "$destination" ] || [ -L "$destination" ]; then
    printf 'refusing to restore over an existing destination: %s\n' "$destination" >&2
    exit 1
  fi
done

# Stage each tree beside its own destination (inside the destination's
# parent, so the final rename is an atomic same-filesystem move).
for ((i = 0; i < ${#snapshot_trees[@]}; i++)); do
  parent="$(dirname -- "${destinations[i]}")"
  mkdir -p -- "$parent"
  test -d "$parent" && test ! -L "$parent" || {
    printf 'missing or unsafe restore destination parent: %s\n' "$parent" >&2
    exit 1
  }
  stages[i]="$(mktemp -d "$parent/.availability-restore-${stage_labels[i]}.XXXXXX")"
  read -r "stage_devs[i]" "stage_inos[i]" < <(stat -c '%d %i' "${stages[i]}")
  stage_owned[i]=1
done

for ((i = 0; i < ${#snapshot_trees[@]}; i++)); do
  rsync -a --no-times --checksum "${sources[i]}/" "${stages[i]}/"
done

# Revalidate the snapshot -- not the stages -- after the copy above, to
# detect any mutation of the archived snapshot that happened concurrently
# with the rsync copy.
require_exact_snapshot_layout "$snapshot"
require_regular_tree "$snapshot"
(cd "$snapshot" && sha256sum --check --strict SHA256SUMS >/dev/null)
validate_layout "$snapshot/raw" "$snapshot/parquet-large/availability" \
  --manifest-sha256 "$manifest_sha256"

# Prove every stage byte-identical to the just-revalidated snapshot tree
# before anything is published.
stage_drift="$(
  for ((i = 0; i < ${#snapshot_trees[@]}; i++)); do
    rsync -a --no-times --checksum --delete --dry-run --itemize-changes \
      "${sources[i]}/" "${stages[i]}/"
  done
)"
if [ -n "$stage_drift" ]; then
  printf 'staged copy differs from the snapshot:\n%s\n' "$stage_drift" >&2
  exit 1
fi

# Publish the five destinations with an explicit rollback state machine: if
# any rename fails, every destination renamed so far is renamed back to its
# original staging name so no final destination remains. If that rollback
# itself fails, every retained path is printed and left untouched --
# ambiguous recovery data is never deleted. Nothing in this span may abort
# the script on its own.
publish_active=1
publish_ok=1
for ((i = 0; i < ${#snapshot_trees[@]}; i++)); do
  if [ -e "${destinations[i]}" ] || [ -L "${destinations[i]}" ]; then
    publish_ok=0
    break
  fi
  if [ "$i" -gt 0 ] && ! du -sB1 "${destinations[i - 1]}" >/dev/null; then
    publish_ok=0
    break
  fi
  publish_index=$i
  mv -T -n "${stages[i]}" "${destinations[i]}" || true
  if [ -d "${stages[i]}" ]; then
    publish_ok=0
    break
  fi
  stage_owned[i]=0
done

if [ "$publish_ok" -ne 1 ]; then
  if rollback_published; then
    printf 'destination publish failed (concurrent creation or rename failure); rolled back every published destination\n' >&2
    exit 1
  fi
  report_fatal_rollback
  exit 1
fi

# Independently validate the restored trees in place; a failure here rolls
# the five destinations back to their stages (which cleanup then removes).
if ! validate_layout "$data_dir/raw" "$data_dir/parquet-large/availability" \
  --manifest-sha256 "$manifest_sha256"
then
  if rollback_published; then
    printf 'post-restore validation failed; rolled back every published destination\n' >&2
    exit 1
  fi
  report_fatal_rollback
  exit 1
fi
publish_active=0

printf 'verified availability restore: data_dir=%s\n' "$data_dir"
for tree in "${snapshot_trees[@]}"; do
  printf '  %s\n' "$data_dir/$tree"
done
printf 'manifest_sha256: %s\n' "$manifest_sha256"
