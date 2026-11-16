#!/usr/bin/env bash
# Reject an image tag that CI could not have produced, before any registry or cluster work.
# CI tags images as <MAJOR.MINOR.PATCH>-<YYYYMMDDHHMMSS>, e.g. 1.0.0-20261116010802
# (see scripts/build-version.sh). The CI run summary prints the tag to copy.
set -euo pipefail

image_tag="${1:-}"
TAG_PATTERN='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-[0-9]{14}$'
[[ "$image_tag" =~ $TAG_PATTERN ]] && exit 0

echo "::error title=Invalid image tag::received '$image_tag'"
if [[ -z "$image_tag" ]]; then
  echo "The image tag is empty. Copy it from the summary of the CI run that published the image." >&2
elif [[ "$image_tag" =~ [[:space:]] ]]; then
  echo "The image tag contains spaces or line breaks (usually from copy-paste). Enter it without them." >&2
elif [[ "$image_tag" == *[:/]* ]]; then
  echo "Enter only the tag, the part after the last ':' (e.g. 1.0.0-20261116010802), not the full image name." >&2
elif [[ "$image_tag" =~ ^[0-9]+\.[0-9]+\.[0-9]+-[0-9]{14}-[0-9]+$ ]]; then
  echo "This is the old tag format with the CI run ID at the end. Re-run CI to publish a tag in the current format." >&2
fi
echo "Expected MAJOR.MINOR.PATCH-YYYYMMDDHHMMSS, for example 1.0.0-20261116010802." >&2
exit 2
