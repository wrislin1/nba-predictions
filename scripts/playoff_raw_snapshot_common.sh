# Shared validation contract for playoff raw-cache archive and restore.

capture_recovery_state() {
  local output="$1"
  local path
  local canonical
  shift

  : > "$output"
  for path in "$@"; do
    if test -L "$path" ||
      { test ! -f "$path" && test ! -d "$path"; }
    then
      printf 'cannot capture unsafe playoff recovery input: %s\n' \
        "$path" >&2
      return 1
    fi
    canonical="$(
      cd "$(dirname -- "$path")" &&
        printf '%s/%s\n' "$(pwd -P)" "$(basename -- "$path")"
    )"
    {
      printf 'path\0%s\0' "$canonical"
      find -P "$path" -mindepth 0 \
        -printf '%D:%i:%y:%m:%s:%T@:%C@:%P\0' |
        LC_ALL=C sort -z
    } >> "$output"
  done
}

assert_recovery_state_unchanged() {
  local expected="$1"
  local label="$2"
  local observed
  shift 2

  observed="$(
    mktemp "${TMPDIR:-/tmp}/.playoff-recovery-state.XXXXXX"
  )"
  if ! capture_recovery_state "$observed" "$@"; then
    rm -f -- "$observed"
    return 1
  fi
  if ! cmp -s -- "$expected" "$observed"; then
    printf 'playoff recovery input changed during operation: %s\n' \
      "$label" >&2
    rm -f -- "$observed"
    return 1
  fi
  rm -f -- "$observed"
}

prepare_restore_destination() {
  local requested_destination="$1"
  local live_source="$2"
  local historical_source="$3"
  local selected_snapshot="$4"
  local requested_parent
  local canonical_parent
  local candidate
  local protected
  local canonical_protected

  test ! -e "$requested_destination" &&
    test ! -L "$requested_destination" || {
      printf 'refusing to replace playoff raw directory: %s\n' \
        "$requested_destination" >&2
      return 1
    }
  requested_parent="$(dirname -- "$requested_destination")"
  test -d "$requested_parent" || {
    printf 'playoff restore parent must already exist: %s\n' \
      "$requested_parent" >&2
    return 1
  }
  canonical_parent="$(cd "$requested_parent" && pwd -P)" || {
    printf 'invalid playoff restore parent: %s\n' \
      "$requested_parent" >&2
    return 1
  }
  candidate="$canonical_parent/$(basename -- "$requested_destination")"

  for protected in \
    "$live_source" "$historical_source" "$selected_snapshot"
  do
    if test -e "$protected" || test -L "$protected"; then
      test ! -L "$protected" && test -d "$protected" || {
        printf 'invalid protected playoff recovery tree: %s\n' \
          "$protected" >&2
        return 1
      }
      canonical_protected="$(cd "$protected" && pwd -P)"
      case "$candidate" in
        "$canonical_protected"|"$canonical_protected"/*)
          printf 'restore destination is within protected playoff recovery tree: %s\n' \
            "$canonical_protected" >&2
          return 1
          ;;
      esac
    fi
  done

  test ! -L "$requested_parent" || {
    printf 'invalid playoff restore parent: %s\n' \
      "$requested_parent" >&2
    return 1
  }
  test ! -e "$candidate" && test ! -L "$candidate" || {
    printf 'refusing to replace playoff raw directory: %s\n' \
      "$candidate" >&2
    return 1
  }
  destination_parent="$canonical_parent"
  destination="$candidate"
}

validate_playoff_cache() {
  local cache="$1"
  local invalid
  local body

  test ! -L "$cache" && test -d "$cache" || {
    printf 'invalid playoff cache structure: %s\n' "$cache" >&2
    return 1
  }
  invalid="$(
    find -P "$cache" -mindepth 1 -maxdepth 1 ! -type f -print -quit
  )"
  test -z "$invalid" || {
    printf 'invalid playoff cache structure: %s\n' "$invalid" >&2
    return 1
  }
  invalid="$(
    find -P "$cache" -mindepth 1 -maxdepth 1 -type f \
      ! \( -name '*.html' -o -name '*.html.meta.json' \) -print -quit
  )"
  test -z "$invalid" || {
    printf 'invalid playoff cache structure: %s\n' "$invalid" >&2
    return 1
  }
  while IFS= read -r -d '' body; do
    test -f "$body.meta.json" && test ! -L "$body.meta.json" || {
      printf 'invalid playoff cache structure: missing sidecar for %s\n' \
        "$body" >&2
      return 1
    }
  done < <(
    find -P "$cache" -mindepth 1 -maxdepth 1 -type f \
      -name '*.html' -print0
  )
  while IFS= read -r -d '' body; do
    test -f "${body%.meta.json}" && test ! -L "${body%.meta.json}" || {
      printf 'invalid playoff cache structure: orphan sidecar %s\n' \
        "$body" >&2
      return 1
    }
  done < <(
    find -P "$cache" -mindepth 1 -maxdepth 1 -type f \
      -name '*.html.meta.json' -print0
  )
}

validate_cache_manifest() {
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
  local seeded_from
  local seed_operation
  local source_sha
  local body
  local sidecar
  local actual_sha
  local actual_count=0
  declare -A expected_bodies=()

  validate_playoff_cache "$cache"
  test ! -L "$manifest" && test -f "$manifest" || {
    printf 'invalid playoff cache manifest: %s\n' "$manifest" >&2
    return 1
  }
  if ! records="$(
    jq -er '
      def play_in_month:
        if .season == 2020 then "august"
        elif .season == 2021 then "may"
        elif (.season >= 2022 and .season <= 2026) then "april"
        else null
        end;
      def expected_filename:
        (
          "NBA_" + (.season | tostring) + "_games"
          + (if .month == null then "" else "-" + .month end)
          + ".html"
        );
      .sources.basketball_reference.playoff_pages as $pages
      | if (($pages | type) != "array") or (($pages | length) != 50) then
          error("playoff pages must contain exactly 50 records")
        elif (
          [$pages[] | select(.resource == "playoff_schedule")] | length
        ) != 43 then
          error("playoff pages must contain 43 dedicated records")
        elif (
          [
            $pages[]
            | select(.resource == "play_in_schedule_month")
          ] | length
        ) != 7 then
          error("playoff pages must contain seven seeded records")
        elif (all($pages[];
          (type == "object")
          and ((.provider | type) == "string")
          and ((.resource | type) == "string")
          and ((.league | type) == "string")
          and ((.season | type) == "number")
          and (
            (.month == null)
            or ((.month | type) == "string")
          )
          and ((.url | type) == "string")
          and ((.path | type) == "string")
          and ((.sha256 | type) == "string")
        ) | not) then
          error("playoff page has invalid field types")
        elif (
          ($pages | map(.path) | unique | length) != ($pages | length)
        ) then
          error("playoff page paths must be unique")
        elif (
          ($pages | map(.url) | unique | length) != ($pages | length)
        ) then
          error("playoff page URLs must be unique")
        elif (
          (
            $pages
            | map([.resource, .season, .month] | @json)
            | unique
            | length
          ) != ($pages | length)
        ) then
          error("playoff resource identities must be unique")
        elif (all($pages[];
          (.provider == "basketball_reference")
          and (.league == "NBA")
          and (.season == (.season | floor))
          and (.sha256 | test("^[0-9a-f]{64}$"))
          and (
            (
              (.resource == "playoff_schedule")
              and (.season >= 1984)
              and (.season <= 2026)
              and (.month == null)
              and (
                .url
                == (
                  "https://www.basketball-reference.com/playoffs/"
                  + expected_filename
                )
              )
            )
            or
            (
              (.resource == "play_in_schedule_month")
              and (play_in_month != null)
              and (.month == play_in_month)
              and (
                .url
                == (
                  "https://www.basketball-reference.com/leagues/"
                  + expected_filename
                )
              )
              and (
                .seeded_from
                == (
                  "raw/bref/NBA_" + (.season | tostring)
                  + "_games-" + .month + ".html"
                )
              )
              and (.seed_operation == "verified_cache_copy")
              and (.source_sha256 == .sha256)
            )
          )
          and (
            .path == ("raw/playoff_bref/" + expected_filename)
          )
        ) | not) then
          error("playoff page identity is invalid")
        else
          $pages[]
          | [
              (.path | sub("^raw/playoff_bref/"; "")),
              .provider,
              .resource,
              .league,
              (.season | tostring),
              (.month // "__index__"),
              .url,
              .sha256,
              (.seeded_from // "__none__"),
              (.seed_operation // "__none__"),
              (.source_sha256 // "__none__")
            ]
          | @tsv
        end
    ' "$manifest"
  )"; then
    printf 'playoff cache does not match manifest: %s\n' "$manifest" >&2
    return 1
  fi

  while IFS=$'\t' read -r \
    filename provider resource league season month url expected_sha \
    seeded_from seed_operation source_sha
  do
    body="$cache/$filename"
    sidecar="$body.meta.json"
    test -f "$body" && test ! -L "$body" || {
      printf 'playoff cache does not match manifest: missing %s\n' \
        "$filename" >&2
      return 1
    }
    actual_sha="$(sha256sum "$body" | cut -d' ' -f1)"
    test "$actual_sha" = "$expected_sha" || {
      printf 'playoff cache does not match manifest: body SHA %s\n' \
        "$filename" >&2
      return 1
    }
    if ! jq -se \
      --arg provider "$provider" \
      --arg resource "$resource" \
      --arg league "$league" \
      --argjson season "$season" \
      --arg month "$month" \
      --arg url "$url" \
      --arg sha256 "$expected_sha" \
      --arg seeded_from "$seeded_from" \
      --arg seed_operation "$seed_operation" \
      --arg source_sha "$source_sha" \
      '
        (length == 1)
        and (
          .[0]
          | (type == "object")
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
          and (
            if $resource == "play_in_schedule_month" then
              (.seeded_from == $seeded_from)
              and (.seed_operation == $seed_operation)
              and (.source_sha256 == $source_sha)
              and (.source_sha256 == .sha256)
            else
              true
            end
          )
        )
      ' "$sidecar" >/dev/null
    then
      printf 'playoff cache does not match manifest: sidecar %s\n' \
        "$filename" >&2
      return 1
    fi
    expected_bodies["$filename"]=1
  done <<< "$records"

  while IFS= read -r -d '' body; do
    filename="${body##*/}"
    test -n "${expected_bodies[$filename]+present}" || {
      printf 'playoff cache does not match manifest: extra %s\n' \
        "$filename" >&2
      return 1
    }
    actual_count=$((actual_count + 1))
  done < <(
    find -P "$cache" -mindepth 1 -maxdepth 1 -type f \
      -name '*.html' -print0
  )
  test "$actual_count" -eq 50 &&
    test "$actual_count" -eq "${#expected_bodies[@]}" || {
      printf 'playoff cache does not match manifest: body inventory\n' >&2
      return 1
    }
}

validate_manifest_pin() {
  local live_manifest="$1"
  local trusted_manifest="$2"
  local expected_sha="$3"
  local label="$4"
  local live_sha
  local trusted_sha

  test ! -L "$live_manifest" && test -f "$live_manifest" || {
    printf 'invalid %s manifest: %s\n' "$label" "$live_manifest" >&2
    return 1
  }
  test ! -L "$trusted_manifest" && test -f "$trusted_manifest" || {
    printf 'invalid trusted %s manifest: %s\n' \
      "$label" "$trusted_manifest" >&2
    return 1
  }
  trusted_sha="$(sha256sum "$trusted_manifest" | cut -d' ' -f1)"
  test "$trusted_sha" = "$expected_sha" || {
    printf '%s manifest pin changed: %s\n' \
      "$label" "$trusted_manifest" >&2
    return 1
  }
  live_sha="$(sha256sum "$live_manifest" | cut -d' ' -f1)"
  test "$live_sha" = "$expected_sha" &&
    cmp -s -- "$live_manifest" "$trusted_manifest" || {
      printf '%s manifest changed: %s\n' "$label" "$live_manifest" >&2
      return 1
    }
}

validate_historical_dependency() {
  local playoff_manifest="$1"
  local trusted_playoff_manifest="$2"
  local playoff_manifest_sha="$3"
  local historical_manifest="$4"
  local trusted_historical_manifest="$5"
  local historical_manifest_sha="$6"
  local historical_cache="$7"
  local historical_validator="$8"
  local pinned_historical_sha

  if ! pinned_historical_sha="$(
    jq -er '
      .sources.basketball_reference.historical_manifest.sha256
      | select(
          (type == "string")
          and test("^[0-9a-f]{64}$")
        )
    ' "$trusted_playoff_manifest"
  )"; then
    printf 'invalid pinned historical manifest SHA: %s\n' \
      "$trusted_playoff_manifest" >&2
    return 1
  fi
  test "$pinned_historical_sha" = "$historical_manifest_sha" || {
    printf 'historical manifest does not match playoff pin: %s\n' \
      "$historical_manifest" >&2
    return 1
  }
  bash "$historical_validator" --validate-cache-manifest \
    "$historical_cache" "$trusted_historical_manifest"
  validate_manifest_pin \
    "$playoff_manifest" "$trusted_playoff_manifest" \
    "$playoff_manifest_sha" "playoff"
  validate_manifest_pin \
    "$historical_manifest" "$trusted_historical_manifest" \
    "$historical_manifest_sha" "historical"
}

validate_snapshot_structure() {
  local snapshot="$1"
  local invalid

  test ! -L "$snapshot" && test -d "$snapshot" || {
    printf 'invalid playoff snapshot structure: %s\n' "$snapshot" >&2
    return 1
  }
  invalid="$(
    find -P "$snapshot" -mindepth 1 -maxdepth 1 \
      ! \( \( -name playoff_bref -a -type d \) -o \
           \( -name playoff_games_manifest.json -a -type f \) -o \
           \( -name SHA256SUMS -a -type f \) \) -print -quit
  )"
  test -z "$invalid" || {
    printf 'invalid playoff snapshot structure: %s\n' "$invalid" >&2
    return 1
  }
  test ! -L "$snapshot/playoff_games_manifest.json" &&
    test -f "$snapshot/playoff_games_manifest.json" &&
    test ! -L "$snapshot/SHA256SUMS" &&
    test -f "$snapshot/SHA256SUMS" || {
      printf 'invalid playoff snapshot structure: %s\n' "$snapshot" >&2
      return 1
    }
  validate_playoff_cache "$snapshot/playoff_bref"
}

write_playoff_checksum_inventory() {
  local snapshot="$1"

  (
    cd "$snapshot"
    find -P playoff_bref -mindepth 1 -maxdepth 1 -type f -print0 \
      | LC_ALL=C sort -z \
      | xargs -0 -r sha256sum
  )
}

validate_snapshot_payload() {
  local candidate="$1"
  local trusted_manifest="$2"
  local checksum_inventory
  local result=0

  validate_snapshot_structure "$candidate"
  cmp -s -- \
    "$trusted_manifest" "$candidate/playoff_games_manifest.json" || {
      printf 'playoff snapshot manifest mismatch: %s\n' "$candidate" >&2
      return 1
    }
  validate_cache_manifest "$candidate/playoff_bref" "$trusted_manifest"
  checksum_inventory="$(
    mktemp "${TMPDIR:-/tmp}/.playoff-checksums.XXXXXX"
  )"
  write_playoff_checksum_inventory "$candidate" > "$checksum_inventory"
  cmp -s -- "$candidate/SHA256SUMS" "$checksum_inventory" || {
    printf 'playoff snapshot checksum inventory mismatch: %s\n' \
      "$candidate" >&2
    result=1
  }
  if test "$result" -eq 0; then
    (
      cd "$candidate"
      sha256sum -c --status SHA256SUMS
    ) || {
      printf 'playoff snapshot checksum verification failed: %s\n' \
        "$candidate" >&2
      result=1
    }
  fi
  rm -f -- "$checksum_inventory"
  return "$result"
}

require_stage_parent() {
  local staging="$1"
  local expected_parent="$2"
  local label="$3"
  local actual_parent

  actual_parent="$(dirname -- "$staging")"
  test "$actual_parent" = "$expected_parent" || {
    printf 'cross-device %s rejected: stage %s is outside %s\n' \
      "$label" "$staging" "$expected_parent" >&2
    return 1
  }
}
