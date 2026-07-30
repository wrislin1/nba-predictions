#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
manifest="$root/data/parquet/build_manifest.json"
test -f "$manifest" || {
  printf 'missing build manifest: %s\n' "$manifest" >&2
  exit 1
}
ending_season="$(jq -er '.tables.games.max_season' "$manifest")"
manifest_sha="$(sha256sum "$manifest" | cut -d' ' -f1)"
archive_root="${NBA_RAW_ARCHIVE_ROOT:-/mnt/nas/llm/documents/nba-predictions-archive}"
destination="$archive_root/slice2a-raw-${ending_season}-${manifest_sha:0:12}"
test ! -e "$destination" || {
  printf 'refusing to overwrite existing snapshot: %s\n' "$destination" >&2
  exit 1
}

mkdir -p "$destination/raw"
cp "$manifest" "$destination/build_manifest.json"
rsync -a --checksum --exclude='/historical_bref/' \
  "$root/data/raw/" "$destination/raw/"
(
  cd "$destination"
  find raw -type f -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)

changes="$(
  rsync -a --checksum --dry-run --itemize-changes --exclude='/historical_bref/' \
    "$root/data/raw/" "$destination/raw/"
)"
test -z "$changes" || {
  printf 'archive differs from source:\n%s\n' "$changes" >&2
  exit 1
}
printf 'verified raw snapshot: %s\n' "$destination"
