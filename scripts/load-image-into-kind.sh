#!/usr/bin/env bash
# Load an image from a registry into a Kind cluster, for the nodes' own platform only.
#
# Why this script exists: CI publishes multi-platform images (linux/amd64 + linux/arm64, plus
# build attestations). `docker pull` downloads only the Mac's platform, but `kind load
# docker-image` exports the whole multi-platform index. containerd on the node then imports
# "all platforms" and fails with "content digest sha256:... not found" for the parts that were
# never downloaded. Pulling the single-platform manifest by its digest avoids that: the image
# loaded into Kind then contains exactly one platform.
#
# Usage: scripts/load-image-into-kind.sh <registry/owner/name:tag> <kind-cluster-name>
# Needs: docker (logged in to the registry if the image is private), kind.
set -euo pipefail

image="${1:?usage: $0 <image:tag> <kind-cluster-name>}"
cluster="${2:?usage: $0 <image:tag> <kind-cluster-name>}"
repository="${image%:*}"

# The nodes' CPU architecture, as the registry names it
node="$(kind get nodes --name "$cluster" | head -n 1)"
[[ -n "$node" ]] || { echo "no nodes found for Kind cluster '$cluster'" >&2; exit 1; }
case "$(docker exec "$node" uname -m)" in
  aarch64 | arm64) arch=arm64 ;;
  x86_64 | amd64) arch=amd64 ;;
  *) echo "unsupported node architecture: $(docker exec "$node" uname -m)" >&2; exit 1 ;;
esac

# For a multi-platform image, find the digest of the linux/<arch> manifest. Attestation
# entries are listed as unknown/unknown, so they never match. A single-platform image has
# no list at all, and is simply pulled as it is.
digest="$(docker buildx imagetools inspect "$image" --format \
  '{{range .Manifest.Manifests}}{{if .Platform}}{{.Platform.OS}}/{{.Platform.Architecture}} {{.Digest}}{{"\n"}}{{end}}{{end}}' \
  | awk -v want="linux/$arch" '$1 == want { print $2; exit }')"

if [[ -n "$digest" ]]; then
  echo "Pulling $image for linux/$arch only ($digest)"
  docker pull "$repository@$digest"
  docker tag "$repository@$digest" "$image"   # the tag now points at that one platform
else
  echo "Pulling $image (single-platform image)"
  docker pull "$image"
fi

kind load docker-image "$image" --name "$cluster"
echo "Loaded $image into Kind cluster '$cluster' (linux/$arch)"
