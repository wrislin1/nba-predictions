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

validate_historical_cache_manifest() {
  local cache="$1"
  local manifest="$2"
  local records
  local filename
  local provider
  local resource
  local league
  local season
  local month
  local url
  local expected_sha
  local body
  local sidecar
  local actual_sha
  local actual_count=0
  declare -A expected_bodies=()

  validate_historical_cache "$cache"
  test ! -L "$manifest" && test -f "$manifest" || {
    printf 'invalid historical cache manifest: %s\n' "$manifest" >&2
    return 1
  }
  if ! records="$(
    jq -er '
      def expected_filename:
        (
          .league + "_" + (.season | tostring) + "_games"
          + (if .month == null then "" else "-" + .month end)
          + ".html"
        );
      .sources.basketball_reference.pages as $pages
      | if (($pages | type) != "array") or (($pages | length) == 0) then
          error("historical pages must be a non-empty array")
        elif (all($pages[];
          (type == "object")
          and ((.provider | type) == "string")
          and ((.resource | type) == "string")
          and ((.league | type) == "string")
          and ((.season | type) == "number")
          and (
            (.month == null)
            or (
              ((.month | type) == "string")
              and (.month | test("^[a-z]+$"))
            )
          )
          and ((.url | type) == "string")
          and ((.path | type) == "string")
          and ((.sha256 | type) == "string")
        ) | not) then
          error("historical page has invalid field types")
        elif (all($pages[];
          (.provider == "basketball_reference")
          and ((.league == "BAA") or (.league == "NBA"))
          and (.season == (.season | floor))
          and (
            (
              (.resource == "historical_schedule_index")
              and (.month == null)
            )
            or (
              (.resource == "historical_schedule_month")
              and (.month != null)
            )
          )
          and (.path == ("raw/historical_bref/" + expected_filename))
          and (
            .url
            == (
              "https://www.basketball-reference.com/leagues/"
              + expected_filename
            )
          )
          and (.sha256 | test("^[0-9a-f]{64}$"))
        ) | not) then
          error("historical page identity is invalid")
        elif (($pages | map(.path) | unique | length) != ($pages | length)) then
          error("historical page paths must be unique")
        else
          $pages[]
          | [
              (.path | sub("^raw/historical_bref/"; "")),
              .provider,
              .resource,
              .league,
              (.season | tostring),
              (.month // "__index__"),
              .url,
              .sha256
            ]
          | @tsv
        end
    ' "$manifest"
  )"; then
    printf 'historical cache does not match manifest: %s\n' "$manifest" >&2
    return 1
  fi

  while IFS=$'\t' read -r \
    filename provider resource league season month url expected_sha
  do
    body="$cache/$filename"
    sidecar="$body.meta.json"
    test -f "$body" && test ! -L "$body" || {
      printf 'historical cache does not match manifest: missing %s\n' \
        "$filename" >&2
      return 1
    }
    actual_sha="$(sha256sum "$body" | cut -d' ' -f1)"
    test "$actual_sha" = "$expected_sha" || {
      printf 'historical cache does not match manifest: body SHA %s\n' \
        "$filename" >&2
      return 1
    }
    if ! jq -e \
      --arg provider "$provider" \
      --arg resource "$resource" \
      --arg league "$league" \
      --argjson season "$season" \
      --arg month "$month" \
      --arg url "$url" \
      --arg sha256 "$expected_sha" \
      '
        (type == "object")
        and (.provider == $provider)
        and (.resource == $resource)
        and (.league == $league)
        and (.season == $season)
        and (
          if $month == "__index__" then
            (.month == null)
          else
            (.month == $month)
          end
        )
        and (.url == $url)
        and (.sha256 == $sha256)
      ' "$sidecar" >/dev/null
    then
      printf 'historical cache does not match manifest: sidecar %s\n' \
        "$filename" >&2
      return 1
    fi
    expected_bodies["$filename"]=1
  done <<< "$records"

  while IFS= read -r -d '' body; do
    filename="${body##*/}"
    test -n "${expected_bodies[$filename]+present}" || {
      printf 'historical cache does not match manifest: extra %s\n' \
        "$filename" >&2
      return 1
    }
    actual_count=$((actual_count + 1))
  done < <(
    find -P "$cache" -mindepth 1 -maxdepth 1 -type f \
      -name '*.html' -print0
  )
  test "$actual_count" -eq "${#expected_bodies[@]}" || {
    printf 'historical cache does not match manifest: body inventory\n' >&2
    return 1
  }
}

if test "${1:-}" = "--validate-cache-manifest"; then
  test "$#" -eq 3 || {
    printf 'usage: %s --validate-cache-manifest CACHE MANIFEST\n' "$0" >&2
    exit 2
  }
  validate_historical_cache_manifest "$2" "$3"
  exit
fi
test "$#" -eq 0 || {
  printf 'usage: %s\n' "$0" >&2
  exit 2
}

root="$(cd "$(dirname "$0")/.." && pwd)"
manifest="${NBA_HISTORICAL_RAW_MANIFEST:-$root/data/parquet/historical_games_manifest.json}"
source="${NBA_HISTORICAL_RAW_SOURCE:-$root/data/raw/historical_bref}"
archive_root="${NBA_HISTORICAL_RAW_ARCHIVE_ROOT:-/mnt/nas/llm/documents/nba-predictions-archive}"

validate_live_manifest_pin() {
  local live_manifest="$1"
  local pinned_manifest="$2"
  local expected_sha="$3"
  local live_sha
  local pinned_sha

  pinned_sha="$(sha256sum "$pinned_manifest" | cut -d' ' -f1)"
  test "$pinned_sha" = "$expected_sha" || {
    printf 'historical manifest pin changed during archive: %s\n' \
      "$pinned_manifest" >&2
    return 1
  }
  live_sha="$(sha256sum "$live_manifest" | cut -d' ' -f1)"
  test "$live_sha" = "$expected_sha" &&
    cmp -s "$live_manifest" "$pinned_manifest" || {
      printf 'historical manifest changed during archive: %s\n' \
        "$live_manifest" >&2
      return 1
    }
}

checksum_inventory=
staging=
trusted_manifest=
cleanup() {
  if test -n "${staging:-}" && test -d "$staging"; then
    rm -rf -- "$staging"
  fi
  if test -n "${checksum_inventory:-}" &&
    test -e "$checksum_inventory"
  then
    rm -f -- "$checksum_inventory"
  fi
  if test -n "${trusted_manifest:-}" && test -e "$trusted_manifest"; then
    rm -f -- "$trusted_manifest"
  fi
}
trap cleanup EXIT

test -f "$manifest" || {
  printf 'missing historical manifest: %s\n' "$manifest" >&2
  exit 1
}
test ! -L "$manifest" || {
  printf 'invalid historical cache manifest: %s\n' "$manifest" >&2
  exit 1
}
test -d "$archive_root" || {
  printf 'missing historical archive root: %s\n' "$archive_root" >&2
  exit 1
}
trusted_manifest="$(
  mktemp "${TMPDIR:-/tmp}/.historical-archive-manifest.XXXXXX"
)"
cp -- "$manifest" "$trusted_manifest"
chmod 400 "$trusted_manifest"
manifest_sha="$(sha256sum "$trusted_manifest" | cut -d' ' -f1)"
validate_live_manifest_pin "$manifest" "$trusted_manifest" "$manifest_sha"

validate_historical_cache "$source"
validate_historical_cache_manifest "$source" "$trusted_manifest"
ending_season="$(
  jq -er '.tables.historical_games.max_season' "$trusted_manifest"
)"
test "$ending_season" = 1983 || {
  printf 'unexpected historical ending season: %s\n' "$ending_season" >&2
  exit 1
}
destination="$archive_root/historical-raw-${ending_season}-${manifest_sha:0:12}"
test ! -e "$destination" || {
  printf 'refusing to overwrite existing snapshot: %s\n' "$destination" >&2
  exit 1
}

staging="$(mktemp -d "$archive_root/.historical-archive.XXXXXX")"
mkdir "$staging/historical_bref"
cp -- "$trusted_manifest" "$staging/historical_games_manifest.json"
chmod 644 "$staging/historical_games_manifest.json"
staged_manifest_sha="$(
  sha256sum "$staging/historical_games_manifest.json" | cut -d' ' -f1
)"
test "$staged_manifest_sha" = "$manifest_sha" || {
  printf 'historical manifest changed while archiving: %s\n' "$manifest" >&2
  exit 1
}
rsync -a --checksum "$source/" "$staging/historical_bref/"
validate_historical_cache_manifest "$source" "$trusted_manifest"
validate_historical_cache_manifest \
  "$staging/historical_bref" "$trusted_manifest"
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
validate_live_manifest_pin "$manifest" "$trusted_manifest" "$manifest_sha"
test -z "$changes" || {
  printf 'historical archive differs from source:\n%s\n' "$changes" >&2
  exit 1
}
test ! -e "$destination" || {
  printf 'refusing to overwrite existing snapshot: %s\n' "$destination" >&2
  exit 1
}
validate_live_manifest_pin "$manifest" "$trusted_manifest" "$manifest_sha"
validate_historical_cache_manifest \
  "$staging/historical_bref" "$trusted_manifest"
cmp -s "$trusted_manifest" "$staging/historical_games_manifest.json" || {
  printf 'staged historical manifest differs from trusted manifest: %s\n' \
    "$staging/historical_games_manifest.json" >&2
  exit 1
}
staged_manifest_sha="$(
  sha256sum "$staging/historical_games_manifest.json" | cut -d' ' -f1
)"
test "$staged_manifest_sha" = "$manifest_sha" || {
  printf 'staged historical manifest hash mismatch: %s\n' \
    "$staging/historical_games_manifest.json" >&2
  exit 1
}
checksum_inventory="$(
  mktemp "$archive_root/.historical-archive.inventory.XXXXXX"
)"
(
  cd "$staging"
  find -P historical_bref -mindepth 1 -maxdepth 1 -type f -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 -r sha256sum
) > "$checksum_inventory"
(
  cd "$staging"
  sha256sum -c --status SHA256SUMS
) || {
  printf 'staged historical checksum verification failed: %s\n' \
    "$staging/SHA256SUMS" >&2
  exit 1
}
cmp -s -- "$staging/SHA256SUMS" "$checksum_inventory" || {
  printf 'staged historical checksum inventory changed: %s\n' \
    "$staging/SHA256SUMS" >&2
  exit 1
}
mv -T -n "$staging" "$destination"
test ! -d "$staging" || {
  printf 'refusing to overwrite existing snapshot: %s\n' "$destination" >&2
  exit 1
}
staging=
rm -f -- "$checksum_inventory"
checksum_inventory=
rm -f -- "$trusted_manifest"
trusted_manifest=
trap - EXIT
printf 'verified historical raw snapshot: %s\n' "$destination"
