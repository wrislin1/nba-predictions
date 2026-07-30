#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
manifest="${NBA_HISTORICAL_RAW_MANIFEST:-$root/data/parquet/historical_games_manifest.json}"
source="${NBA_HISTORICAL_RAW_SOURCE:-$root/data/raw/historical_bref}"
archive_root="${NBA_HISTORICAL_RAW_ARCHIVE_ROOT:-/mnt/nas/llm/documents/nba-predictions-archive}"

test -f "$manifest" || {
  printf 'missing historical manifest: %s\n' "$manifest" >&2
  exit 1
}
test -d "$source" || {
  printf 'missing historical raw cache: %s\n' "$source" >&2
  exit 1
}
test -d "$archive_root" || {
  printf 'missing historical archive root: %s\n' "$archive_root" >&2
  exit 1
}
ending_season="$(jq -er '.tables.historical_games.max_season' "$manifest")"
test "$ending_season" = 1983 || {
  printf 'unexpected historical ending season: %s\n' "$ending_season" >&2
  exit 1
}
manifest_sha="$(sha256sum "$manifest" | cut -d' ' -f1)"
destination="$archive_root/historical-raw-${ending_season}-${manifest_sha:0:12}"
test ! -e "$destination" || {
  printf 'refusing to overwrite existing snapshot: %s\n' "$destination" >&2
  exit 1
}
mkdir "$destination"
mkdir "$destination/historical_bref"
cp "$manifest" "$destination/historical_games_manifest.json"
rsync -a --checksum "$source/" "$destination/historical_bref/"
(
  cd "$destination"
  find historical_bref -type f -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 -r sha256sum > SHA256SUMS
  sha256sum -c --status SHA256SUMS
)
changes="$(
  rsync -a --checksum --dry-run --itemize-changes --delete \
    "$source/" "$destination/historical_bref/"
)"
test -z "$changes" || {
  printf 'historical archive differs from source:\n%s\n' "$changes" >&2
  exit 1
}
printf 'verified historical raw snapshot: %s\n' "$destination"
