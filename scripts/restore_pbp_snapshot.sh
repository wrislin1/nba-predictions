#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C
umask 077

root="$(cd "$(dirname "$0")/.." && pwd)"
data_dir="${NBA_PBP_DATA_DIR:-$root/data}"
snapshot_arg="${NBA_PBP_SNAPSHOT:-}"
manifest_sha256="${NBA_PBP_MANIFEST_SHA256:-}"
raw_destination="${NBA_PBP_RAW_DESTINATION:-$data_dir/raw/stats_nba/pbp}"
publication_destination="${NBA_PBP_PUBLICATION_DESTINATION:-$data_dir/parquet-large/pbp}"

export PYTHONPATH="$root/src${PYTHONPATH:+:$PYTHONPATH}"
python_bin="$root/.venv/bin/python"

test "$#" -eq 0 || {
  printf 'usage: %s\n' "$0" >&2
  exit 2
}

test -n "$snapshot_arg" || {
  printf 'NBA_PBP_SNAPSHOT is required\n' >&2
  exit 1
}
test -n "$manifest_sha256" || {
  printf 'NBA_PBP_MANIFEST_SHA256 is required\n' >&2
  exit 1
}
printf '%s' "$manifest_sha256" | grep -Eq '^[0-9a-f]{64}$' || {
  printf 'NBA_PBP_MANIFEST_SHA256 must be a 64-character lowercase hex digest\n' >&2
  exit 1
}

test -d "$snapshot_arg" && test ! -L "$snapshot_arg" || {
  printf 'missing or unsafe snapshot directory: %s\n' "$snapshot_arg" >&2
  exit 1
}
snapshot="$(cd "$snapshot_arg" && pwd -P)"

require_exact_snapshot_layout() {
  local dir="$1"
  local entries expected
  entries="$(cd "$dir" && find . -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort)"
  expected="$(printf 'SHA256SUMS\nparquet-large\nraw\n' | LC_ALL=C sort)"
  if [ "$entries" != "$expected" ]; then
    printf 'unexpected snapshot top-level entries in %s:\n%s\n' "$dir" "$entries" >&2
    return 1
  fi
  test -d "$dir/raw/stats_nba/pbp" && test ! -L "$dir/raw/stats_nba/pbp" || {
    printf 'missing or unsafe raw/stats_nba/pbp under %s\n' "$dir" >&2
    return 1
  }
  test -d "$dir/parquet-large/pbp" && test ! -L "$dir/parquet-large/pbp" || {
    printf 'missing or unsafe parquet-large/pbp under %s\n' "$dir" >&2
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
  test "$(cd "$dir/raw/stats_nba" && find . -mindepth 1 -maxdepth 1 -printf '%f\n')" = "pbp" || {
    printf 'unexpected entries under %s/raw/stats_nba\n' "$dir" >&2
    return 1
  }
  test "$(cd "$dir/parquet-large" && find . -mindepth 1 -maxdepth 1 -printf '%f\n')" = "pbp" || {
    printf 'unexpected entries under %s/parquet-large\n' "$dir" >&2
    return 1
  }
}

require_exact_snapshot_layout "$snapshot"

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

validate_sha256sums_grammar "$snapshot"
(cd "$snapshot" && sha256sum --check --strict SHA256SUMS >/dev/null)

snapshot_manifest_sha256="$(
  sha256sum "$snapshot/parquet-large/pbp/_manifest.json" | cut -d' ' -f1
)"
if [ "$snapshot_manifest_sha256" != "$manifest_sha256" ]; then
  printf 'trusted manifest digest does not match the snapshot manifest\n' >&2
  exit 1
fi

"$python_bin" -m nba_data.pbp_snapshot validate-layout \
  --raw-root "$snapshot/raw/stats_nba/pbp" \
  --publication-root "$snapshot/parquet-large/pbp" \
  --manifest-sha256 "$manifest_sha256"

for destination in "$raw_destination" "$publication_destination"; do
  if [ -e "$destination" ] || [ -L "$destination" ]; then
    printf 'refusing to restore over an existing destination: %s\n' "$destination" >&2
    exit 1
  fi
done

raw_parent="$(dirname -- "$raw_destination")"
publication_parent="$(dirname -- "$publication_destination")"
for parent in "$raw_parent" "$publication_parent"; do
  test -d "$parent" && test ! -L "$parent" || {
    printf 'missing or unsafe restore destination parent: %s\n' "$parent" >&2
    exit 1
  }
done

raw_allocated_bytes="$(
  du -sB1 "$snapshot/raw/stats_nba/pbp" | awk '{print $1}'
)"
publication_allocated_bytes="$(
  du -sB1 "$snapshot/parquet-large/pbp" | awk '{print $1}'
)"

raw_device="$(stat -c '%d' "$raw_parent")"
publication_device="$(stat -c '%d' "$publication_parent")"

declare -A device_allocated_bytes
declare -A device_parent
device_allocated_bytes["$raw_device"]=$(( ${device_allocated_bytes["$raw_device"]:-0} + raw_allocated_bytes ))
device_parent["$raw_device"]="$raw_parent"
device_allocated_bytes["$publication_device"]=$(( ${device_allocated_bytes["$publication_device"]:-0} + publication_allocated_bytes ))
device_parent["$publication_device"]="$publication_parent"

for device in "${!device_allocated_bytes[@]}"; do
  parent="${device_parent[$device]}"
  read -r device_total_bytes device_available_bytes < <(
    df -B1 --output=size,avail "$parent" | tail -n1
  )
  required_bytes=$(( device_allocated_bytes[$device] + device_total_bytes / 5 ))
  if [ "$device_available_bytes" -lt "$required_bytes" ]; then
    printf 'insufficient restore capacity on device %s at %s: required %d bytes, available %d bytes\n' \
      "$device" "$parent" "$required_bytes" "$device_available_bytes" >&2
    exit 1
  fi
done

raw_stage=""
publication_stage=""
raw_stage_owned=0
publication_stage_owned=0

cleanup() {
  if [ "$raw_stage_owned" -eq 1 ] && [ -n "$raw_stage" ] \
    && [ ! -L "$raw_stage" ] && [ -d "$raw_stage" ]
  then
    rm -rf -- "$raw_stage"
  fi
  if [ "$publication_stage_owned" -eq 1 ] && [ -n "$publication_stage" ] \
    && [ ! -L "$publication_stage" ] && [ -d "$publication_stage" ]
  then
    rm -rf -- "$publication_stage"
  fi
}
trap cleanup INT TERM EXIT

raw_stage="$(mktemp -d "$raw_parent/.pbp-restore-raw.XXXXXX")"
raw_stage_owned=1
publication_stage="$(mktemp -d "$publication_parent/.pbp-restore-publication.XXXXXX")"
publication_stage_owned=1

rsync -a --no-times --checksum "$snapshot/raw/stats_nba/pbp/" "$raw_stage/"
rsync -a --no-times --checksum "$snapshot/parquet-large/pbp/" "$publication_stage/"

"$python_bin" -m nba_data.pbp_snapshot validate-layout \
  --raw-root "$raw_stage" \
  --publication-root "$publication_stage" \
  --manifest-sha256 "$manifest_sha256"

"$python_bin" -m nba_data.pbp_snapshot rebuild-compare \
  --raw-root "$raw_stage" \
  --publication-root "$publication_stage" \
  --stage-parent "$raw_parent"

# Publish the two destinations with an explicit two-step rollback state
# machine: if the second rename fails, the first destination is renamed
# back to its original staging name so neither final destination remains.
# If that rollback itself fails, every retained path is printed and left
# untouched -- ambiguous recovery data is never deleted.
raw_stage_original="$raw_stage"

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

"$python_bin" -m nba_data.pbp_snapshot validate-layout \
  --raw-root "$raw_destination" \
  --publication-root "$publication_destination" \
  --manifest-sha256 "$manifest_sha256"

printf 'verified pbp restore: raw=%s publication=%s\n' \
  "$raw_destination" "$publication_destination"
