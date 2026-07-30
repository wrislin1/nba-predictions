#!/usr/bin/env bash
set -euo pipefail

validate_historical_cache() {
  local cache="$1"
  local invalid
  local body

  test ! -L "$cache" && test -d "$cache" || {
    printf 'invalid historical snapshot structure: %s\n' "$cache" >&2
    return 1
  }
  invalid="$(
    find -P "$cache" -mindepth 1 -maxdepth 1 ! -type f -print -quit
  )"
  test -z "$invalid" || {
    printf 'invalid historical snapshot structure: %s\n' "$invalid" >&2
    return 1
  }
  invalid="$(
    find -P "$cache" -mindepth 1 -maxdepth 1 -type f \
      ! \( -name '*.html' -o -name '*.html.meta.json' \) -print -quit
  )"
  test -z "$invalid" || {
    printf 'invalid historical snapshot structure: %s\n' "$invalid" >&2
    return 1
  }
  while IFS= read -r -d '' body; do
    test -f "$body.meta.json" && test ! -L "$body.meta.json" || {
      printf 'invalid historical snapshot structure: missing sidecar for %s\n' \
        "$body" >&2
      return 1
    }
  done < <(
    find -P "$cache" -mindepth 1 -maxdepth 1 -type f -name '*.html' -print0
  )
  while IFS= read -r -d '' body; do
    test -f "${body%.meta.json}" && test ! -L "${body%.meta.json}" || {
      printf 'invalid historical snapshot structure: orphan sidecar %s\n' \
        "$body" >&2
      return 1
    }
  done < <(
    find -P "$cache" -mindepth 1 -maxdepth 1 -type f \
      -name '*.html.meta.json' -print0
  )
}

validate_snapshot() {
  local snapshot="$1"
  local invalid

  test ! -L "$snapshot" && test -d "$snapshot" || {
    printf 'invalid historical snapshot structure: %s\n' "$snapshot" >&2
    return 1
  }
  invalid="$(
    find -P "$snapshot" -mindepth 1 -maxdepth 1 \
      ! \( \( -name historical_bref -a -type d \) -o \
           \( -name historical_games_manifest.json -a -type f \) -o \
           \( -name SHA256SUMS -a -type f \) \) -print -quit
  )"
  test -z "$invalid" || {
    printf 'invalid historical snapshot structure: %s\n' "$invalid" >&2
    return 1
  }
  validate_historical_cache "$snapshot/historical_bref"
}

root="$(cd "$(dirname "$0")/.." && pwd)"
manifest="${NBA_HISTORICAL_RAW_MANIFEST:-$root/data/parquet/historical_games_manifest.json}"
archive_root="${NBA_HISTORICAL_RAW_ARCHIVE_ROOT:-/mnt/nas/llm/documents/nba-predictions-archive}"
destination="${NBA_HISTORICAL_RAW_DESTINATION:-$root/data/raw/historical_bref}"
cache_manifest_validator="$root/scripts/archive_historical_raw_snapshot.sh"

validate_snapshot_payload() {
  local candidate="$1"
  local trusted_manifest="$2"
  local required
  local expected_checksums
  local actual_checksums

  for required in \
    "$candidate/historical_games_manifest.json" \
    "$candidate/SHA256SUMS"
  do
    test -f "$required" || {
      printf 'incomplete historical snapshot: %s\n' "$required" >&2
      return 1
    }
  done
  test -d "$candidate/historical_bref" || {
    printf 'incomplete historical snapshot: %s\n' \
      "$candidate/historical_bref" >&2
    return 1
  }
  validate_snapshot "$candidate"
  cmp -s "$trusted_manifest" "$candidate/historical_games_manifest.json" || {
    printf 'historical snapshot manifest mismatch: %s\n' "$candidate" >&2
    return 1
  }
  expected_checksums="$(< "$candidate/SHA256SUMS")"
  actual_checksums="$(
    cd "$candidate"
    find -P historical_bref -mindepth 1 -maxdepth 1 -type f -print0 \
      | LC_ALL=C sort -z \
      | xargs -0 -r sha256sum
  )"
  test "$actual_checksums" = "$expected_checksums" || {
    printf 'historical snapshot checksum mismatch: %s\n' "$candidate" >&2
    return 1
  }
  (
    cd "$candidate"
    sha256sum -c --status SHA256SUMS
  )
  bash "$cache_manifest_validator" --validate-cache-manifest \
    "$candidate/historical_bref" "$trusted_manifest"
}

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

validate_snapshot_payload "$snapshot" "$manifest"
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
bash "$cache_manifest_validator" --validate-cache-manifest \
  "$staging" "$manifest"
validate_snapshot_payload "$snapshot" "$manifest"
changes="$(
  rsync -a --checksum --dry-run --itemize-changes --delete \
    "$snapshot/historical_bref/" "$staging/"
)"
test -z "$changes" || {
  printf 'restored historical cache differs:\n%s\n' "$changes" >&2
  exit 1
}
test ! -e "$destination" || {
  printf 'refusing to replace historical raw directory: %s\n' "$destination" >&2
  exit 1
}
mv -T -n "$staging" "$destination"
test ! -d "$staging" || {
  printf 'refusing to replace historical raw directory: %s\n' "$destination" >&2
  exit 1
}
staging=
trap - EXIT
printf 'verified historical raw restore: %s\n' "$destination"
