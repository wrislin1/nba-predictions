#!/usr/bin/env bash
# Archive the immutable availability snapshot: the four raw trees the
# availability build reads plus the parquet-large/availability publication,
# under $NBA_AVAILABILITY_ARCHIVE_ROOT/availability-archive-2026-<sha12>/
# with a sorted SHA256SUMS inventory. The player_games publication the
# build was bound to is read for validation only and never archived; the
# manifest names the D1 snapshot that restores it.
#
# TODO(task-22): the real archive of the ~6 GB live tree and the restore
# test into an absent directory on /home run on the owner's machine against
# the NAS (NBA_AVAILABILITY_ARCHIVE_ROOT default); this container has no
# data/ tree, so only the unit tests exercise both scripts here.
set -euo pipefail
export LC_ALL=C
umask 077

root="$(cd "$(dirname "$0")/.." && pwd)"
NBA_AVAILABILITY_ARCHIVE_ROOT=${NBA_AVAILABILITY_ARCHIVE_ROOT:-/mnt/nas/llm/documents/nba-predictions-archive}
NBA_AVAILABILITY_DATA_DIR=${NBA_AVAILABILITY_DATA_DIR:-data}
archive_root="$NBA_AVAILABILITY_ARCHIVE_ROOT"
data_dir="$NBA_AVAILABILITY_DATA_DIR"
raw_source="$data_dir/raw"
publication_source="$data_dir/parquet-large/availability"
player_games_root="$data_dir/parquet-large/player_games"

# The five archived trees, relative to both $data_dir and the snapshot root.
snapshot_trees=(
  raw/stats_nba/box_rosters
  raw/nba_injury_reports
  raw/sportsdataverse/nba_stats_injury_report
  raw/nba_gamebooks
  parquet-large/availability
)

export PYTHONPATH="$root/src${PYTHONPATH:+:$PYTHONPATH}"
python_bin="$root/.venv/bin/python"

test "$#" -eq 0 || {
  printf 'usage: %s\n' "$0" >&2
  exit 2
}

require_regular_tree() {
  # Reject any lstat-visible entry that is neither a regular file nor a
  # directory (symlink, FIFO, socket, device, or otherwise unsafe/stale).
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

write_checksum_inventory() {
  local dir="$1"
  (
    cd "$dir"
    find . -type f ! -name SHA256SUMS -printf '%P\0' \
      | LC_ALL=C sort -z \
      | xargs -0 -r sha256sum
  )
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

staging=""
staging_dev=""
staging_ino=""
staging_owned=0

# Cleanup removes only the exact real directory this process itself
# created, matched by the device/inode identity captured right after
# `mktemp` returned it -- never anything else that merely occupies the
# staging path, which could be a concurrent replacement this run does
# not own.
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
  if [ "$staging_owned" -eq 1 ]; then
    remove_owned_stage "$staging" "$staging_dev" "$staging_ino"
  fi
}
# A signal trap that does not itself call exit does not stop the script --
# bash runs the trap body and then RESUMES execution at the interrupted
# point. Registering `cleanup` directly against INT/TERM would run cleanup
# and then let the archive continue to completion, silently ignoring a
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

# A stale staging entry left behind by a previous crashed/killed run is
# never silently ignored or reused: it is rejected outright, since it may
# hold a partial or unsafe copy and mktemp would only ever create a new,
# differently named sibling next to it.
stale_staging="$(
  find "$archive_root" -mindepth 1 -maxdepth 1 -name '.availability-archive.*' -print -quit
)"
if [ -n "$stale_staging" ]; then
  printf 'refusing to archive: stale staging entry present: %s\n' "$stale_staging" >&2
  exit 1
fi

for tree in "${snapshot_trees[@]}"; do
  test -d "$data_dir/$tree" && test ! -L "$data_dir/$tree" || {
    printf 'missing or unsafe source tree: %s\n' "$data_dir/$tree" >&2
    exit 1
  }
done

# Delegate every JSON/Parquet/PDF semantic question about the live source to
# the dedicated offline module; this script only ever orchestrates the
# filesystem side of archiving an already-valid tree.
validate_layout "$raw_source" "$publication_source"

for tree in "${snapshot_trees[@]}"; do
  require_regular_tree "$data_dir/$tree"
done

source_paths=()
for tree in "${snapshot_trees[@]}"; do
  source_paths+=("$data_dir/$tree")
done
source_allocated_bytes="$(
  du -sB1 "${source_paths[@]}" \
    | awk '{sum += $1} END {print sum}'
)"
read -r destination_total_bytes destination_available_bytes < <(
  df -B1 --output=size,avail "$archive_root" | tail -n1
)
required_bytes=$((source_allocated_bytes + destination_total_bytes / 5))
if [ "$destination_available_bytes" -lt "$required_bytes" ]; then
  printf 'insufficient archive capacity: required %d bytes, available %d bytes\n' \
    "$required_bytes" "$destination_available_bytes" >&2
  exit 1
fi

staging="$(mktemp -d "$archive_root/.availability-archive.XXXXXX")"
test ! -L "$staging" && test -d "$staging" || {
  printf 'failed to create a real staging directory under %s\n' "$archive_root" >&2
  exit 1
}
read -r staging_dev staging_ino < <(stat -c '%d %i' "$staging")
staging_owned=1

for tree in "${snapshot_trees[@]}"; do
  mkdir -p "$staging/$tree"
  rsync -a --no-times --checksum "$data_dir/$tree/" "$staging/$tree/"
done

# Validate the freshly staged copy semantically before it is trusted at all.
validate_layout "$staging/raw" "$staging/parquet-large/availability"

write_checksum_inventory "$staging" > "$staging/SHA256SUMS"
(cd "$staging" && sha256sum --check --strict SHA256SUMS >/dev/null)

manifest_sha256="$(
  sha256sum "$staging/parquet-large/availability/_manifest.json" | cut -d' ' -f1
)"

# Revalidate the *live* source -- not the stage -- against the manifest
# digest the stage was just proven to have, to detect any mutation of the
# live raw trees or publication that happened concurrently with the rsync
# copy above.
validate_layout "$raw_source" "$publication_source" --manifest-sha256 "$manifest_sha256"

# The manifest binds every raw pair the build consumed, but byte-compare the
# live trees against the just-checksummed stage as well so ANY live mutation
# since the copy -- bound or not, coherent or not -- fails the archive.
live_drift="$(
  for tree in "${snapshot_trees[@]}"; do
    rsync -a --no-times --checksum --delete --dry-run --itemize-changes \
      "$data_dir/$tree/" "$staging/$tree/"
  done
)"
if [ -n "$live_drift" ]; then
  printf 'live source changed during archive copy:\n%s\n' "$live_drift" >&2
  exit 1
fi

snapshot_name="availability-archive-2026-${manifest_sha256:0:12}"
snapshot="$archive_root/$snapshot_name"

if [ -e "$snapshot" ] || [ -L "$snapshot" ]; then
  # An identically named snapshot already exists. Never modify it: succeed
  # only if it already matches the freshly staged copy exactly, and fail
  # otherwise without touching it in any way.
  test ! -L "$snapshot" && test -d "$snapshot" || {
    printf 'existing snapshot entry is not a real directory: %s\n' "$snapshot" >&2
    exit 1
  }
  require_exact_snapshot_layout "$snapshot"
  require_regular_tree "$snapshot"
  (cd "$snapshot" && sha256sum --check --strict SHA256SUMS >/dev/null)
  existing_manifest_sha256="$(
    sha256sum "$snapshot/parquet-large/availability/_manifest.json" | cut -d' ' -f1
  )"
  if [ "$existing_manifest_sha256" != "$manifest_sha256" ]; then
    printf 'existing snapshot manifest digest differs: %s\n' "$snapshot" >&2
    exit 1
  fi
  validate_layout "$snapshot/raw" "$snapshot/parquet-large/availability" \
    --manifest-sha256 "$manifest_sha256"
  changes="$(
    rsync -a --no-times --checksum --delete --dry-run --itemize-changes \
      "$staging/" "$snapshot/"
  )"
  if [ -n "$changes" ]; then
    printf 'existing snapshot differs from the freshly staged source:\n%s\n' \
      "$changes" >&2
    exit 1
  fi
  remove_owned_stage "$staging" "$staging_dev" "$staging_ino"
  staging_owned=0
  printf 'verified availability snapshot: %s\n' "$snapshot"
  printf 'manifest_sha256: %s\n' "$manifest_sha256"
  exit 0
fi

# A final staging-integrity probe immediately precedes the last-moment
# concurrency check below, giving the atomic-rename decision one last real
# disk touch point before it commits.
du -sB1 "$staging" >/dev/null

# Final concurrency check: fail closed if another process published this
# exact snapshot name between the check above and now, rather than risking
# a clobber.
if [ -e "$snapshot" ] || [ -L "$snapshot" ]; then
  printf 'refusing to overwrite existing snapshot (concurrent creation): %s\n' \
    "$snapshot" >&2
  exit 1
fi

mv -T -n "$staging" "$snapshot" || true
if [ -d "$staging" ]; then
  printf 'refusing to overwrite existing snapshot (concurrent creation or rename failure): %s\n' \
    "$snapshot" >&2
  exit 1
fi
staging_owned=0

printf 'verified availability snapshot: %s\n' "$snapshot"
printf 'manifest_sha256: %s\n' "$manifest_sha256"
