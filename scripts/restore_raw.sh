#!/usr/bin/env bash
set -euo pipefail

archive=/mnt/nas/llm/documents/nba-predictions-archive/pre-rewrite-e0f8e3a/raw/venv-static-data
destination="$(dirname "$0")/../data/raw/legacy"

mkdir -p "$destination"
rsync -a --checksum "$archive/nba-teams-1984-2019-ordered.csv" "$destination/"
rsync -a --checksum "$archive/league_boxscores_extra.csv" "$destination/"
printf '%s  %s\n' \
  3a71f75829452032523098280542485f2a94513facde2ea3f78b5b4c4356764c \
  "$destination/nba-teams-1984-2019-ordered.csv" \
  e512c1a15b5a2046c47efb86152b1087c28c8955b183fdf5c0e8151876f8b822 \
  "$destination/league_boxscores_extra.csv" | sha256sum -c -
