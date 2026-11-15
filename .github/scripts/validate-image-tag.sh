#!/usr/bin/env bash
# Reject malformed image tags before any registry or cluster operation.
set -euo pipefail

image_tag="${1:-}"
if [[ ! "$image_tag" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
  echo 'image_tag must be a valid container image tag.' >&2
  exit 2
fi
