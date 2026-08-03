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
raw_source="$data_dir/raw/stats_nba/player_logs"
publication_source="$data_dir/parquet-large/player_games"

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

write_checksum_inventory() {
  local dir="$1"
  (
    cd "$dir"
    find . -type f ! -name SHA256SUMS -printf '%P\0' \
      | LC_ALL=C sort -z \
      | xargs -0 -r sha256sum
  )
}

staging=
cleanup() {
  if [ -n "${staging:-}" ] && [ ! -L "$staging" ] && [ -d "$staging" ]; then
    rm -rf -- "$staging"
  fi
}
# A signal trap that does not itself call exit does not stop the script --
# bash runs the trap body and then RESUMES execution at the interrupted
# point. Registering `cleanup` directly against INT/TERM (as a combined
# `trap cleanup INT TERM EXIT`) would run cleanup and then let the archive
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

# A stale staging entry left behind by a previous crashed/killed run is
# never silently ignored or reused: it is rejected outright, since it may
# hold a partial or unsafe copy and mktemp would only ever create a new,
# differently named sibling next to it.
stale_staging="$(
  find "$archive_root" -mindepth 1 -maxdepth 1 -name '.player-games-archive.*' -print -quit
)"
if [ -n "$stale_staging" ]; then
  printf 'refusing to archive: stale staging entry present: %s\n' "$stale_staging" >&2
  exit 1
fi

# Delegate every JSON/Parquet semantic question about the live source to the
# dedicated offline module; this script only ever orchestrates the
# filesystem side of archiving an already-valid pair.
"$python_bin" -m nba_data.player_games_snapshot validate-layout \
  --raw-root "$raw_source" \
  --publication-root "$publication_source" \
  --reference-dir "$reference_dir"

require_regular_tree "$raw_source"
require_regular_tree "$publication_source"

source_allocated_bytes="$(
  du -sB1 "$raw_source" "$publication_source" \
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

staging="$(mktemp -d "$archive_root/.player-games-archive.XXXXXX")"
test ! -L "$staging" && test -d "$staging" || {
  printf 'failed to create a real staging directory under %s\n' "$archive_root" >&2
  exit 1
}

mkdir -p "$staging/raw/stats_nba/player_logs" "$staging/parquet-large/player_games"
rsync -a --no-times --checksum "$raw_source/" "$staging/raw/stats_nba/player_logs/"
rsync -a --no-times --checksum "$publication_source/" "$staging/parquet-large/player_games/"

# Validate the freshly staged copy semantically before it is trusted at all.
"$python_bin" -m nba_data.player_games_snapshot validate-layout \
  --raw-root "$staging/raw/stats_nba/player_logs" \
  --publication-root "$staging/parquet-large/player_games" \
  --reference-dir "$reference_dir"

write_checksum_inventory "$staging" > "$staging/SHA256SUMS"
(cd "$staging" && sha256sum --check --strict SHA256SUMS >/dev/null)

manifest_sha256="$(
  sha256sum "$staging/parquet-large/player_games/_manifest.json" | cut -d' ' -f1
)"

# Revalidate the *live* source -- not the stage -- against the manifest
# digest the stage was just proven to have, to detect any mutation of the
# live raw archive or publication that happened concurrently with the
# rsync copy above.
"$python_bin" -m nba_data.player_games_snapshot validate-layout \
  --raw-root "$raw_source" \
  --publication-root "$publication_source" \
  --reference-dir "$reference_dir" \
  --manifest-sha256 "$manifest_sha256"

snapshot_name="player-games-archive-2026-${manifest_sha256:0:12}"
snapshot="$archive_root/$snapshot_name"

if [ -e "$snapshot" ] || [ -L "$snapshot" ]; then
  # An identically named snapshot already exists. Never modify it: succeed
  # only if it already matches the freshly staged copy exactly, and fail
  # otherwise without touching it in any way.
  require_exact_snapshot_layout "$snapshot"
  (cd "$snapshot" && sha256sum --check --strict SHA256SUMS >/dev/null)
  existing_manifest_sha256="$(
    sha256sum "$snapshot/parquet-large/player_games/_manifest.json" | cut -d' ' -f1
  )"
  if [ "$existing_manifest_sha256" != "$manifest_sha256" ]; then
    printf 'existing snapshot manifest digest differs: %s\n' "$snapshot" >&2
    exit 1
  fi
  "$python_bin" -m nba_data.player_games_snapshot validate-layout \
    --raw-root "$snapshot/raw/stats_nba/player_logs" \
    --publication-root "$snapshot/parquet-large/player_games" \
    --reference-dir "$reference_dir" \
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
  rm -rf -- "$staging"
  staging=
  printf 'verified player-games snapshot: %s\n' "$snapshot"
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
staging=

printf 'verified player-games snapshot: %s\n' "$snapshot"
printf 'manifest_sha256: %s\n' "$manifest_sha256"
