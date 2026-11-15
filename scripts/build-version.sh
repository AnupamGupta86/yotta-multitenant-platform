#!/usr/bin/env bash
set -euo pipefail

version_base="$(tr -d '[:space:]' < VERSION)"
if [[ ! "$version_base" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "VERSION must contain major.minor.patch, for example 1.0.0" >&2
  exit 2
fi

timestamp="$(date -u +'%Y%m%d%H%M%S')"
run_id="${GITHUB_RUN_ID:-$(python3 -c 'import uuid; print(uuid.uuid4().hex[:8])')}"
printf '%s-%s-%s\n' "$version_base" "$timestamp" "$run_id"
