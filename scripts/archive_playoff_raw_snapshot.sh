#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
manifest="${NBA_PLAYOFF_RAW_MANIFEST:-$root/data/parquet/playoff_games_manifest.json}"
source="${NBA_PLAYOFF_RAW_SOURCE:-$root/data/raw/playoff_bref}"
archive_root="${NBA_PLAYOFF_RAW_ARCHIVE_ROOT:-/mnt/nas/llm/documents/nba-predictions-archive}"
historical_manifest="${NBA_PLAYOFF_HISTORICAL_MANIFEST:-$root/data/parquet/historical_games_manifest.json}"
historical_cache="${NBA_PLAYOFF_HISTORICAL_CACHE:-$root/data/raw/historical_bref}"
historical_validator="$root/scripts/archive_historical_raw_snapshot.sh"

# shellcheck source=playoff_raw_snapshot_common.sh
source "$root/scripts/playoff_raw_snapshot_common.sh"

test "$#" -eq 0 || {
  printf 'usage: %s\n' "$0" >&2
  exit 2
}

staging=
trusted_manifest=
trusted_historical_manifest=
protected_state=
snapshot_state=
payload_state=
cleanup() {
  if test -n "${staging:-}" && test -d "$staging"; then
    rm -rf -- "$staging"
  fi
  if test -n "${trusted_manifest:-}" && test -e "$trusted_manifest"; then
    rm -f -- "$trusted_manifest"
  fi
  if test -n "${trusted_historical_manifest:-}" &&
    test -e "$trusted_historical_manifest"
  then
    rm -f -- "$trusted_historical_manifest"
  fi
  for state_file in \
    "${protected_state:-}" "${snapshot_state:-}" "${payload_state:-}"
  do
    if test -n "$state_file" && test -e "$state_file"; then
      rm -f -- "$state_file"
    fi
  done
}
trap cleanup EXIT

test ! -L "$manifest" && test -f "$manifest" || {
  printf 'missing or unsafe playoff manifest: %s\n' "$manifest" >&2
  exit 1
}
test ! -L "$historical_manifest" && test -f "$historical_manifest" || {
  printf 'missing or unsafe historical manifest: %s\n' \
    "$historical_manifest" >&2
  exit 1
}
test ! -L "$archive_root" && test -d "$archive_root" || {
  printf 'missing or unsafe playoff archive root: %s\n' \
    "$archive_root" >&2
  exit 1
}
archive_root="$(cd "$archive_root" && pwd -P)"

trusted_manifest="$(
  mktemp "${TMPDIR:-/tmp}/.playoff-archive-manifest.XXXXXX"
)"
cp -- "$manifest" "$trusted_manifest"
chmod 400 "$trusted_manifest"
manifest_sha="$(sha256sum "$trusted_manifest" | cut -d' ' -f1)"
validate_manifest_pin \
  "$manifest" "$trusted_manifest" "$manifest_sha" "playoff"

trusted_historical_manifest="$(
  mktemp "${TMPDIR:-/tmp}/.playoff-archive-historical-manifest.XXXXXX"
)"
cp -- "$historical_manifest" "$trusted_historical_manifest"
chmod 400 "$trusted_historical_manifest"
historical_manifest_sha="$(
  sha256sum "$trusted_historical_manifest" | cut -d' ' -f1
)"
validate_manifest_pin \
  "$historical_manifest" "$trusted_historical_manifest" \
  "$historical_manifest_sha" "historical"

snapshot="$archive_root/playoff-raw-2026-${manifest_sha:0:12}"

protected_state="$(
  mktemp "${TMPDIR:-/tmp}/.playoff-archive-protected-state.XXXXXX"
)"
capture_recovery_state \
  "$protected_state" \
  "$manifest" "$historical_manifest" "$source" "$historical_cache"
validate_cache_manifest "$source" "$trusted_manifest"
validate_historical_dependency \
  "$manifest" "$trusted_manifest" "$manifest_sha" \
  "$historical_manifest" "$trusted_historical_manifest" \
  "$historical_manifest_sha" "$historical_cache" \
  "$historical_validator"
assert_recovery_state_unchanged \
  "$protected_state" "archive source or historical dependency" \
  "$manifest" "$historical_manifest" "$source" "$historical_cache"

if test -e "$snapshot" || test -L "$snapshot"; then
  snapshot_state="$(
    mktemp "${TMPDIR:-/tmp}/.playoff-archive-snapshot-state.XXXXXX"
  )"
  capture_recovery_state "$snapshot_state" "$snapshot"
  validate_snapshot_payload "$snapshot" "$trusted_manifest"
  changes="$(
    rsync -a --checksum --dry-run --itemize-changes --delete \
      "$source/" "$snapshot/playoff_bref/"
  )"
  test -z "$changes" || {
    printf 'existing playoff snapshot differs from source:\n%s\n' \
      "$changes" >&2
    exit 1
  }
  validate_cache_manifest "$source" "$trusted_manifest"
  validate_snapshot_payload "$snapshot" "$trusted_manifest"
  validate_historical_dependency \
    "$manifest" "$trusted_manifest" "$manifest_sha" \
    "$historical_manifest" "$trusted_historical_manifest" \
    "$historical_manifest_sha" "$historical_cache" \
    "$historical_validator"
  assert_recovery_state_unchanged \
    "$protected_state" "archive source or historical dependency" \
    "$manifest" "$historical_manifest" "$source" "$historical_cache"
  assert_recovery_state_unchanged \
    "$snapshot_state" "existing playoff snapshot" "$snapshot"
  rm -f -- \
    "$trusted_manifest" "$trusted_historical_manifest" \
    "$protected_state" "$snapshot_state"
  trusted_manifest=
  trusted_historical_manifest=
  protected_state=
  snapshot_state=
  trap - EXIT
  printf 'verified playoff raw snapshot: %s\n' "$snapshot"
  exit
fi

staging="$(mktemp -d "$archive_root/.playoff-archive.XXXXXX")"
require_stage_parent "$staging" "$archive_root" "publication"
mkdir "$staging/playoff_bref"
cp -- "$trusted_manifest" "$staging/playoff_games_manifest.json"
chmod 644 "$staging/playoff_games_manifest.json"
rsync -a --checksum "$source/" "$staging/playoff_bref/"
write_playoff_checksum_inventory "$staging" > "$staging/SHA256SUMS"
payload_state="$(
  mktemp "${TMPDIR:-/tmp}/.playoff-archive-payload-state.XXXXXX"
)"
capture_recovery_state "$payload_state" "$staging"

validate_cache_manifest "$source" "$trusted_manifest"
validate_snapshot_payload "$staging" "$trusted_manifest"
validate_historical_dependency \
  "$manifest" "$trusted_manifest" "$manifest_sha" \
  "$historical_manifest" "$trusted_historical_manifest" \
  "$historical_manifest_sha" "$historical_cache" \
  "$historical_validator"
changes="$(
  rsync -a --checksum --dry-run --itemize-changes --delete \
    "$source/" "$staging/playoff_bref/"
)"
test -z "$changes" || {
  printf 'playoff archive differs from source:\n%s\n' "$changes" >&2
  exit 1
}
test ! -e "$snapshot" && test ! -L "$snapshot" || {
  printf 'refusing to overwrite existing playoff snapshot: %s\n' \
    "$snapshot" >&2
  exit 1
}
validate_cache_manifest "$source" "$trusted_manifest"
validate_snapshot_payload "$staging" "$trusted_manifest"
validate_historical_dependency \
  "$manifest" "$trusted_manifest" "$manifest_sha" \
  "$historical_manifest" "$trusted_historical_manifest" \
  "$historical_manifest_sha" "$historical_cache" \
  "$historical_validator"
assert_recovery_state_unchanged \
  "$protected_state" "archive source or historical dependency" \
  "$manifest" "$historical_manifest" "$source" "$historical_cache"
assert_recovery_state_unchanged \
  "$payload_state" "staged playoff snapshot" "$staging"

mv -T -n "$staging" "$snapshot"
test ! -d "$staging" || {
  printf 'refusing to overwrite existing playoff snapshot: %s\n' \
    "$snapshot" >&2
  exit 1
}
staging=
rm -f -- \
  "$trusted_manifest" "$trusted_historical_manifest" \
  "$protected_state" "$payload_state"
trusted_manifest=
trusted_historical_manifest=
protected_state=
payload_state=
trap - EXIT
printf 'verified playoff raw snapshot: %s\n' "$snapshot"
