#!/usr/bin/env bash
set -euo pipefail

IMAGE="${IMAGE:-ghcr.io/owner/website-app:latest}"
EVENT_IMAGE="${EVENT_IMAGE:-yotta/event-consumer:local}"
TENANTS="${TENANTS:-user1 user2 user3}"
for tenant in $TENANTS; do
  ./scripts/deploy-tenant.sh "$tenant" "$tenant.localtest.me" "$IMAGE" "$EVENT_IMAGE"
done
