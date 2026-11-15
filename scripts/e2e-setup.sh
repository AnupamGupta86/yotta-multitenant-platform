#!/usr/bin/env bash
# Recreate the local Kind cluster and install the platform, Provisioning API, and sample tenant; run with ./scripts/e2e-setup.sh [tenant] [domain].
set -euo pipefail

# Configuration defaults. Override image/host defaults with environment variables,
# or pass the tenant and domain as positional arguments.
DEFAULT_TENANT="${DEFAULT_TENANT:-user1}"
DEFAULT_DOMAIN_SUFFIX="${DEFAULT_DOMAIN_SUFFIX:-myyotta.com}"
CLUSTER_NAME="${CLUSTER_NAME:-yotta-platform}"
CLUSTER_DELETE_CONFIRMATION="delete ${CLUSTER_NAME}"
PROVISIONING_HOST="${PROVISIONING_HOST:-provisioning.myyotta.com}"
PROVISIONING_IMAGE="${PROVISIONING_IMAGE:-yotta/website-provisioning:local}"
WEBSITE_IMAGE="${WEBSITE_IMAGE:-yotta/website-app:local}"
WEBSITE_ORCHESTRATOR_IMAGE="${WEBSITE_ORCHESTRATOR_IMAGE:-yotta/website-orchestrator:local}"
PROVISIONING_CHART="${PROVISIONING_CHART:-helm/website-provisioning}"
WEBSITE_CHART="${WEBSITE_CHART:-helm/website}"
PROVISIONING_BUILD_CONTEXT="${PROVISIONING_BUILD_CONTEXT:-services/website-provisioning}"
WEBSITE_BUILD_CONTEXT="${WEBSITE_BUILD_CONTEXT:-services/website-app}"
PROVISIONING_RELEASE="${PROVISIONING_RELEASE:-website-provisioning}"
PLATFORM_NAMESPACE="${PLATFORM_NAMESPACE:-platform}"
PROVISIONING_DEPLOYMENT="${PROVISIONING_DEPLOYMENT:-website-provisioning}"
PROVISIONING_ROLLOUT_TIMEOUT="${PROVISIONING_ROLLOUT_TIMEOUT:-5m}"
PROVISIONING_HELM_TIMEOUT="${PROVISIONING_HELM_TIMEOUT:-10m}"
LOCAL_HTTPS_PORT="${LOCAL_HTTPS_PORT:-8443}"
LOCAL_HOST_IP="${LOCAL_HOST_IP:-127.0.0.1}"
REQUIRED_TOOLS=(docker kind kubectl helm curl)
SETUP_KIND_SCRIPT="./scripts/setup-kind.sh"
DEPLOY_TENANT_SCRIPT="./scripts/deploy-tenant.sh"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
cd "$repo_root"

tenant="${1:-$DEFAULT_TENANT}"
domain="${2:-${tenant}.${DEFAULT_DOMAIN_SUFFIX}}"
typed_confirmation=""

for tool in "${REQUIRED_TOOLS[@]}"; do
  command -v "$tool" >/dev/null || { echo "Install $tool and retry" >&2; exit 1; }
done
docker info >/dev/null 2>&1 || { echo "Start Docker Desktop before setup" >&2; exit 1; }

if [[ -z "${ORCHESTRATOR_REPOSITORY:-}" ]]; then
  remote="$(git remote get-url origin 2>/dev/null || true)"
  ORCHESTRATOR_REPOSITORY="$(printf '%s' "$remote" | sed -E 's#^(https://github\.com/|git@github\.com:)##; s#\.git$##')"
  if [[ -z "$ORCHESTRATOR_REPOSITORY" || "$ORCHESTRATOR_REPOSITORY" == "$remote" ]]; then
    echo "Set ORCHESTRATOR_REPOSITORY to owner/repository" >&2
    exit 2
  fi
  export ORCHESTRATOR_REPOSITORY
fi

if [[ -z "${ORCHESTRATOR_GITHUB_TOKEN:-}" ]]; then
  if [[ ! -t 0 ]]; then
    echo "Set ORCHESTRATOR_GITHUB_TOKEN to a fine-grained token with Actions read/write access" >&2
    exit 2
  fi
  read -r -s -p "GitHub fine-grained token (Actions: read/write): " ORCHESTRATOR_GITHUB_TOKEN
  printf '\n'
  [[ -n "$ORCHESTRATOR_GITHUB_TOKEN" ]] || { echo "A GitHub token is required" >&2; exit 2; }
  export ORCHESTRATOR_GITHUB_TOKEN
fi
trap 'unset ORCHESTRATOR_GITHUB_TOKEN' EXIT

echo "Checking chart definitions..."
helm lint "$PROVISIONING_CHART"
helm lint "$WEBSITE_CHART"

if kind get clusters | grep -qx "$CLUSTER_NAME"; then
  echo "WARNING: deleting Kind cluster $CLUSTER_NAME permanently removes its workloads and stored demo data."
  if [[ ! -t 0 ]]; then
    echo "Refusing to delete a cluster without an interactive confirmation. Run this script in a terminal and type: $CLUSTER_DELETE_CONFIRMATION" >&2
    exit 2
  fi
  read -r -p "Type '$CLUSTER_DELETE_CONFIRMATION' to continue: " typed_confirmation
  if [[ "$typed_confirmation" != "$CLUSTER_DELETE_CONFIRMATION" ]]; then
    echo "Confirmation did not match; leaving cluster $CLUSTER_NAME untouched." >&2
    exit 2
  fi
  echo "Deleting confirmed Kind cluster $CLUSTER_NAME..."
  kind delete cluster --name "$CLUSTER_NAME"
fi

echo "Creating a fresh Kind cluster and installing add-ons, monitoring, and platform..."
CLUSTER_NAME="$CLUSTER_NAME" "$SETUP_KIND_SCRIPT"

echo "Building and loading the local Provisioning API image..."
docker build -t "$PROVISIONING_IMAGE" "$PROVISIONING_BUILD_CONTEXT"
kind load docker-image "$PROVISIONING_IMAGE" --name "$CLUSTER_NAME"

provisioning_tag="${PROVISIONING_IMAGE##*:}"
provisioning_repository="${PROVISIONING_IMAGE%:*}"
helm upgrade --install "$PROVISIONING_RELEASE" "$PROVISIONING_CHART" \
  --namespace "$PLATFORM_NAMESPACE" --create-namespace \
  --set-string "image.repository=$provisioning_repository" \
  --set-string "image.tag=$provisioning_tag" \
  --set-string "ingress.host=$PROVISIONING_HOST" \
  --wait --timeout "$PROVISIONING_HELM_TIMEOUT"
kubectl rollout status "deployment/$PROVISIONING_DEPLOYMENT" \
  --namespace "$PLATFORM_NAMESPACE" --timeout="$PROVISIONING_ROLLOUT_TIMEOUT"

echo "Building and loading the local Website image..."
docker build -t "$WEBSITE_IMAGE" "$WEBSITE_BUILD_CONTEXT"
kind load docker-image "$WEBSITE_IMAGE" --name "$CLUSTER_NAME"

echo "Deploying tenant $tenant ($domain)..."
PULL_IMAGE=false "$DEPLOY_TENANT_SCRIPT" "$tenant" "$domain" "$WEBSITE_IMAGE" "$WEBSITE_ORCHESTRATOR_IMAGE"

cat <<EOF

End-to-end local setup is ready.
Provisioning API: https://${PROVISIONING_HOST}:${LOCAL_HTTPS_PORT}
Tenant website:   https://${domain}:${LOCAL_HTTPS_PORT}

For local name resolution, add this line to /etc/hosts if it is not present:
${LOCAL_HOST_IP} ${PROVISIONING_HOST} ${domain}
EOF
