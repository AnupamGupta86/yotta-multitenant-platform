#!/usr/bin/env bash
# Build a unique CI image version from VERSION, UTC time, and the workflow run ID; run with ./scripts/build-version.sh.
set -euo pipefail

VERSION_FILE="${VERSION_FILE:-VERSION}"
VERSION_PATTERN='^[0-9]+\.[0-9]+\.[0-9]+$'
TIMESTAMP_FORMAT='%Y%m%d%H%M%S'
FALLBACK_RUN_ID_LENGTH=8
version_base="$(tr -d '[:space:]' < "$VERSION_FILE")"
timestamp="$(date -u +"$TIMESTAMP_FORMAT")"
run_id="${GITHUB_RUN_ID:-}"
if [[ -z "$run_id" ]]; then
  run_id="$(python3 -c 'import sys,uuid; print(uuid.uuid4().hex[:int(sys.argv[1])])' "$FALLBACK_RUN_ID_LENGTH")"
fi

if [[ ! "$version_base" =~ $VERSION_PATTERN ]]; then
  echo "VERSION must contain major.minor.patch, for example 1.0.0" >&2
  exit 2
fi

printf '%s-%s-%s\n' "$version_base" "$timestamp" "$run_id"
