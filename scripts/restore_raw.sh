#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
manifest="${NBA_RAW_MANIFEST:-$root/data/parquet/build_manifest.json}"
archive_root="${NBA_RAW_ARCHIVE_ROOT:-/mnt/nas/llm/documents/nba-predictions-archive}"
destination="${NBA_RAW_DESTINATION:-$root/data/raw}"

# shellcheck source=publication_read_lock.sh
source "$root/scripts/publication_read_lock.sh"

staging=
trusted_manifest=
cleanup() {
  if test -n "${staging:-}" && test -d "$staging"; then
    rm -rf -- "$staging"
  fi
  if test -n "${trusted_manifest:-}" && test -e "$trusted_manifest"; then
    rm -f -- "$trusted_manifest"
  fi
  release_publication_read_locks || true
}
trap cleanup EXIT

acquire_publication_read_locks "$manifest"
if ! trusted_manifest="$(
  capture_publication_manifest "$manifest" \
    "${TMPDIR:-/tmp}/.raw-restore-manifest.XXXXXX"
)"; then
  printf 'missing or unsafe build manifest: %s\n' "$manifest" >&2
  exit 1
fi
ending_season="$(jq -er '.tables.games.max_season' "$trusted_manifest")"
manifest_sha="$(sha256sum "$trusted_manifest" | cut -d' ' -f1)"
snapshot="$archive_root/slice2a-raw-${ending_season}-${manifest_sha:0:12}"

for required in \
  "$snapshot/build_manifest.json" \
  "$snapshot/SHA256SUMS" \
  "$snapshot/raw"
do
  test -e "$required" || {
    printf 'incomplete raw snapshot: %s\n' "$required" >&2
    exit 1
  }
done

expected_checksums="$(< "$snapshot/SHA256SUMS")"
actual_checksums="$(
  cd "$snapshot"
  find raw -type f -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 sha256sum
)"
test "$actual_checksums" = "$expected_checksums" || {
  printf 'raw snapshot checksum inventory mismatch: %s\n' "$snapshot" >&2
  exit 1
}
cmp -s "$trusted_manifest" "$snapshot/build_manifest.json" || {
  printf 'snapshot manifest does not match publication: %s\n' "$snapshot" >&2
  exit 1
}
test ! -e "$destination" || {
  printf 'refusing to replace existing raw directory: %s\n' "$destination" >&2
  exit 1
}

destination_parent="$(dirname "$destination")"
mkdir -p "$destination_parent"
staging="$(mktemp -d "$destination_parent/.raw-restore.XXXXXX")"

rsync -a --checksum "$snapshot/raw/" "$staging/"
changes="$(
  rsync -a --checksum --dry-run --itemize-changes --delete \
    "$snapshot/raw/" "$staging/"
)"
test -z "$changes" || {
  printf 'restored raw cache differs from snapshot:\n%s\n' "$changes" >&2
  exit 1
}
mv "$staging" "$destination"
staging=
rm -f -- "$trusted_manifest"
trusted_manifest=
release_publication_read_locks
trap - EXIT

printf 'verified raw restore: %s\n' "$destination"
