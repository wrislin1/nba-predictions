#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
manifest="${NBA_PLAYOFF_RAW_MANIFEST:-$root/data/parquet/playoff_games_manifest.json}"
source="${NBA_PLAYOFF_RAW_SOURCE:-$root/data/raw/playoff_bref}"
destination="${NBA_PLAYOFF_RAW_DESTINATION:-$source}"
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
  for state_file in "${protected_state:-}" "${payload_state:-}"; do
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
  mktemp "${TMPDIR:-/tmp}/.playoff-restore-manifest.XXXXXX"
)"
cp -- "$manifest" "$trusted_manifest"
chmod 400 "$trusted_manifest"
manifest_sha="$(sha256sum "$trusted_manifest" | cut -d' ' -f1)"
validate_manifest_pin \
  "$manifest" "$trusted_manifest" "$manifest_sha" "playoff"

trusted_historical_manifest="$(
  mktemp "${TMPDIR:-/tmp}/.playoff-restore-historical-manifest.XXXXXX"
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
prepare_restore_destination \
  "$destination" "$source" "$historical_cache" "$snapshot"

protected_paths=(
  "$manifest"
  "$historical_manifest"
  "$historical_cache"
  "$snapshot"
)
if test -e "$source" || test -L "$source"; then
  protected_paths+=("$source")
fi
protected_state="$(
  mktemp "${TMPDIR:-/tmp}/.playoff-restore-protected-state.XXXXXX"
)"
capture_recovery_state "$protected_state" "${protected_paths[@]}"
validate_snapshot_payload "$snapshot" "$trusted_manifest"
validate_historical_dependency \
  "$manifest" "$trusted_manifest" "$manifest_sha" \
  "$historical_manifest" "$trusted_historical_manifest" \
  "$historical_manifest_sha" "$historical_cache" \
  "$historical_validator"
assert_recovery_state_unchanged \
  "$protected_state" "restore snapshot or historical dependency" \
  "${protected_paths[@]}"

staging="$(mktemp -d "$destination_parent/.playoff-restore.XXXXXX")"
require_stage_parent "$staging" "$destination_parent" "restore"
rsync -a --checksum "$snapshot/playoff_bref/" "$staging/"
payload_state="$(
  mktemp "${TMPDIR:-/tmp}/.playoff-restore-payload-state.XXXXXX"
)"
capture_recovery_state "$payload_state" "$staging"
validate_cache_manifest "$staging" "$trusted_manifest"
validate_snapshot_payload "$snapshot" "$trusted_manifest"
changes="$(
  rsync -a --checksum --dry-run --itemize-changes --delete \
    "$snapshot/playoff_bref/" "$staging/"
)"
test -z "$changes" || {
  printf 'restored playoff cache differs:\n%s\n' "$changes" >&2
  exit 1
}
test ! -e "$destination" && test ! -L "$destination" || {
  printf 'refusing to replace playoff raw directory: %s\n' \
    "$destination" >&2
  exit 1
}
validate_snapshot_payload "$snapshot" "$trusted_manifest"
validate_cache_manifest "$staging" "$trusted_manifest"
validate_historical_dependency \
  "$manifest" "$trusted_manifest" "$manifest_sha" \
  "$historical_manifest" "$trusted_historical_manifest" \
  "$historical_manifest_sha" "$historical_cache" \
  "$historical_validator"
assert_recovery_state_unchanged \
  "$protected_state" "restore snapshot or historical dependency" \
  "${protected_paths[@]}"
assert_recovery_state_unchanged \
  "$payload_state" "staged playoff restore" "$staging"

mv -T -n "$staging" "$destination"
test ! -d "$staging" || {
  printf 'refusing to replace playoff raw directory: %s\n' \
    "$destination" >&2
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
printf 'verified playoff raw restore: %s\n' "$destination"
