#!/usr/bin/env bash
# Print the image tag for this build: <VERSION>-<UTC build time to the second>,
# e.g. 1.0.0-20261116010802. Used by every CI pipeline; run with ./scripts/build-version.sh.
set -euo pipefail

VERSION_FILE="${VERSION_FILE:-VERSION}"
# MAJOR.MINOR.PATCH, numbers without leading zeros (1.0.0, 1.10.2; not 01.0.0)
VERSION_PATTERN='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'

version_base="$(tr -d '[:space:]' < "$VERSION_FILE")"
if [[ ! "$version_base" =~ $VERSION_PATTERN ]]; then
  echo "VERSION must contain MAJOR.MINOR.PATCH, for example 1.0.0 (found '$version_base')" >&2
  exit 2
fi

printf '%s-%s\n' "$version_base" "$(date -u +%Y%m%d%H%M%S)"
