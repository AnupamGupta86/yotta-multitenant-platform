#!/usr/bin/env bash
# Print the newest published MAJOR.MINOR.PATCH-YYYYMMDDHHMMSS tag of one GHCR image; empty when none exists.
# Usage: newest-image-tag.sh <image-name>   Needs: CONTAINER_REGISTRY, GITHUB_REPOSITORY_OWNER, GHCR_TOKEN.
set -euo pipefail

image_name="${1:?usage: newest-image-tag.sh <image-name>}"
owner="$(printf '%s' "$GITHUB_REPOSITORY_OWNER" | tr '[:upper:]' '[:lower:]')"
TAG_PATTERN='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-[0-9]{14}$'
# GHCR's registry API takes the GitHub token base64-encoded as a bearer token
token="$(printf '%s' "$GHCR_TOKEN" | base64 | tr -d '\n')"
curl -fsSL -H "Authorization: Bearer $token" \
    "https://$CONTAINER_REGISTRY/v2/$owner/$image_name/tags/list?n=1000" \
  | python3 -c 'import json,sys; print("\n".join(json.load(sys.stdin).get("tags") or []))' \
  | grep -E "$TAG_PATTERN" | sort -V | tail -n 1 || true
