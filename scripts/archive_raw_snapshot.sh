#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
manifest="${NBA_RAW_MANIFEST:-$root/data/parquet/build_manifest.json}"

# shellcheck source=publication_read_lock.sh
source "$root/scripts/publication_read_lock.sh"

trusted_manifest=
cleanup() {
  if test -n "${trusted_manifest:-}" && test -e "$trusted_manifest"; then
    rm -f -- "$trusted_manifest"
  fi
  release_publication_read_locks || true
}
trap cleanup EXIT

acquire_publication_read_locks "$manifest"
if ! trusted_manifest="$(
  capture_publication_manifest "$manifest" \
    "${TMPDIR:-/tmp}/.raw-archive-manifest.XXXXXX"
)"; then
  printf 'missing or unsafe build manifest: %s\n' "$manifest" >&2
  exit 1
fi
ending_season="$(jq -er '.tables.games.max_season' "$trusted_manifest")"
manifest_sha="$(sha256sum "$trusted_manifest" | cut -d' ' -f1)"
archive_root="${NBA_RAW_ARCHIVE_ROOT:-/mnt/nas/llm/documents/nba-predictions-archive}"
destination="$archive_root/slice2a-raw-${ending_season}-${manifest_sha:0:12}"
test ! -e "$destination" || {
  printf 'refusing to overwrite existing snapshot: %s\n' "$destination" >&2
  exit 1
}

mkdir -p "$destination/raw"
cp "$trusted_manifest" "$destination/build_manifest.json"
chmod 644 "$destination/build_manifest.json"
rsync -a --checksum \
  --exclude='/historical_bref/' \
  --exclude='/playoff_bref/' \
  "$root/data/raw/" "$destination/raw/"
(
  cd "$destination"
  find raw -type f -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)

changes="$(
  rsync -a --checksum --dry-run --itemize-changes \
    --exclude='/historical_bref/' \
    --exclude='/playoff_bref/' \
    "$root/data/raw/" "$destination/raw/"
)"
test -z "$changes" || {
  printf 'archive differs from source:\n%s\n' "$changes" >&2
  exit 1
}
rm -f -- "$trusted_manifest"
trusted_manifest=
release_publication_read_locks
trap - EXIT
printf 'verified raw snapshot: %s\n' "$destination"
