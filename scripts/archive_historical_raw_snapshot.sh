#!/usr/bin/env bash
set -euo pipefail

validate_historical_cache() {
  local cache="$1"
  local invalid
  local body

  test ! -L "$cache" && test -d "$cache" || {
    printf 'invalid historical cache structure: %s\n' "$cache" >&2
    return 1
  }
  invalid="$(
    find -P "$cache" -mindepth 1 -maxdepth 1 ! -type f -print -quit
  )"
  test -z "$invalid" || {
    printf 'invalid historical cache structure: %s\n' "$invalid" >&2
    return 1
  }
  invalid="$(
    find -P "$cache" -mindepth 1 -maxdepth 1 -type f \
      ! \( -name '*.html' -o -name '*.html.meta.json' \) -print -quit
  )"
  test -z "$invalid" || {
    printf 'invalid historical cache structure: %s\n' "$invalid" >&2
    return 1
  }
  while IFS= read -r -d '' body; do
    test -f "$body.meta.json" && test ! -L "$body.meta.json" || {
      printf 'invalid historical cache structure: missing sidecar for %s\n' \
        "$body" >&2
      return 1
    }
  done < <(
    find -P "$cache" -mindepth 1 -maxdepth 1 -type f -name '*.html' -print0
  )
  while IFS= read -r -d '' body; do
    test -f "${body%.meta.json}" && test ! -L "${body%.meta.json}" || {
      printf 'invalid historical cache structure: orphan sidecar %s\n' \
        "$body" >&2
      return 1
    }
  done < <(
    find -P "$cache" -mindepth 1 -maxdepth 1 -type f \
      -name '*.html.meta.json' -print0
  )
}

root="$(cd "$(dirname "$0")/.." && pwd)"
manifest="${NBA_HISTORICAL_RAW_MANIFEST:-$root/data/parquet/historical_games_manifest.json}"
source="${NBA_HISTORICAL_RAW_SOURCE:-$root/data/raw/historical_bref}"
archive_root="${NBA_HISTORICAL_RAW_ARCHIVE_ROOT:-/mnt/nas/llm/documents/nba-predictions-archive}"

test -f "$manifest" || {
  printf 'missing historical manifest: %s\n' "$manifest" >&2
  exit 1
}
test -d "$archive_root" || {
  printf 'missing historical archive root: %s\n' "$archive_root" >&2
  exit 1
}
validate_historical_cache "$source"
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

staging=
cleanup() {
  if test -n "${staging:-}" && test -d "$staging"; then
    rm -rf -- "$staging"
  fi
}
trap cleanup EXIT
staging="$(mktemp -d "$archive_root/.historical-archive.XXXXXX")"
mkdir "$staging/historical_bref"
cp "$manifest" "$staging/historical_games_manifest.json"
rsync -a --checksum "$source/" "$staging/historical_bref/"
validate_historical_cache "$source"
validate_historical_cache "$staging/historical_bref"
(
  cd "$staging"
  find -P historical_bref -mindepth 1 -maxdepth 1 -type f -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 -r sha256sum > SHA256SUMS
  sha256sum -c --status SHA256SUMS
)
changes="$(
  rsync -a --checksum --dry-run --itemize-changes --delete \
    "$source/" "$staging/historical_bref/"
)"
test -z "$changes" || {
  printf 'historical archive differs from source:\n%s\n' "$changes" >&2
  exit 1
}
test ! -e "$destination" || {
  printf 'refusing to overwrite existing snapshot: %s\n' "$destination" >&2
  exit 1
}
mv -T -n "$staging" "$destination"
test ! -d "$staging" || {
  printf 'refusing to overwrite existing snapshot: %s\n' "$destination" >&2
  exit 1
}
staging=
trap - EXIT
printf 'verified historical raw snapshot: %s\n' "$destination"
