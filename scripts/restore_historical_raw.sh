#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
manifest="${NBA_HISTORICAL_RAW_MANIFEST:-$root/data/parquet/historical_games_manifest.json}"
archive_root="${NBA_HISTORICAL_RAW_ARCHIVE_ROOT:-/mnt/nas/llm/documents/nba-predictions-archive}"
destination="${NBA_HISTORICAL_RAW_DESTINATION:-$root/data/raw/historical_bref}"

test -f "$manifest" || {
  printf 'missing historical manifest: %s\n' "$manifest" >&2
  exit 1
}
ending_season="$(jq -er '.tables.historical_games.max_season' "$manifest")"
test "$ending_season" = 1983 || {
  printf 'unexpected historical ending season: %s\n' "$ending_season" >&2
  exit 1
}
manifest_sha="$(sha256sum "$manifest" | cut -d' ' -f1)"
snapshot="$archive_root/historical-raw-${ending_season}-${manifest_sha:0:12}"

for required in \
  "$snapshot/historical_games_manifest.json" \
  "$snapshot/SHA256SUMS"
do
  test -f "$required" || {
    printf 'incomplete historical snapshot: %s\n' "$required" >&2
    exit 1
  }
done
test -d "$snapshot/historical_bref" || {
  printf 'incomplete historical snapshot: %s\n' "$snapshot/historical_bref" >&2
  exit 1
}
cmp -s "$manifest" "$snapshot/historical_games_manifest.json" || {
  printf 'historical snapshot manifest mismatch: %s\n' "$snapshot" >&2
  exit 1
}
(
  cd "$snapshot"
  sha256sum -c --status SHA256SUMS
)
expected_checksums="$(< "$snapshot/SHA256SUMS")"
actual_checksums="$(
  cd "$snapshot"
  find historical_bref -type f -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 -r sha256sum
)"
test "$actual_checksums" = "$expected_checksums" || {
  printf 'historical snapshot checksum mismatch: %s\n' "$snapshot" >&2
  exit 1
}
test ! -e "$destination" || {
  printf 'refusing to replace historical raw directory: %s\n' "$destination" >&2
  exit 1
}

destination_parent="$(dirname "$destination")"
mkdir -p "$destination_parent"
staging="$(mktemp -d "$destination_parent/.historical-restore.XXXXXX")"
cleanup() {
  if test -n "${staging:-}" && test -d "$staging"; then
    rm -rf -- "$staging"
  fi
}
trap cleanup EXIT
rsync -a --checksum "$snapshot/historical_bref/" "$staging/"
changes="$(
  rsync -a --checksum --dry-run --itemize-changes --delete \
    "$snapshot/historical_bref/" "$staging/"
)"
test -z "$changes" || {
  printf 'restored historical cache differs:\n%s\n' "$changes" >&2
  exit 1
}
mv "$staging" "$destination"
staging=
trap - EXIT
printf 'verified historical raw restore: %s\n' "$destination"
