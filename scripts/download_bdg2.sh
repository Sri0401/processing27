#!/usr/bin/env bash
# Download the Building Data Genome Project 2 files used by the platform.
# Source: https://github.com/buds-lab/building-data-genome-project-2 (CC BY 4.0)
set -euo pipefail

DEST="${1:-data/raw/bdg2}"
BASE="https://media.githubusercontent.com/media/buds-lab/building-data-genome-project-2/master/data"
FILES=(metadata/metadata.csv weather/weather.csv meters/cleaned/gas_cleaned.csv meters/cleaned/electricity_cleaned.csv)

mkdir -p "$DEST"
for f in "${FILES[@]}"; do
  out="$DEST/$(basename "$f")"
  if [[ -s "$out" ]]; then echo "exists  $out"; continue; fi
  echo "fetch   $f"
  curl -sSfL --retry 3 -o "$out" "$BASE/$f"
done
