#!/usr/bin/env bash
# Deploy the website chart for each configured sample tenant; run with ./scripts/deploy-sample-tenants.sh after building or publishing the images.
set -euo pipefail

DEFAULT_WEBSITE_IMAGE=ghcr.io/owner/website-app:latest
DEFAULT_ORCHESTRATOR_IMAGE=yotta/website-orchestrator:local
DEFAULT_TENANTS='user1 user2 user3'
LOCAL_DOMAIN_SUFFIX=myyotta.com
DEPLOY_TENANT_SCRIPT=./scripts/deploy-tenant.sh
IMAGE="${IMAGE:-$DEFAULT_WEBSITE_IMAGE}"
ORCHESTRATOR_IMAGE="${ORCHESTRATOR_IMAGE:-$DEFAULT_ORCHESTRATOR_IMAGE}"
TENANTS="${TENANTS:-$DEFAULT_TENANTS}"
read -r -a tenant_list <<< "$TENANTS"

for tenant in "${tenant_list[@]}"; do
  "$DEPLOY_TENANT_SCRIPT" "$tenant" "$tenant.$LOCAL_DOMAIN_SUFFIX" "$IMAGE" "$ORCHESTRATOR_IMAGE"
done
