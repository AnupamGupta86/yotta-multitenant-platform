#!/usr/bin/env bash
# Recreate the local Kind cluster and install the platform, Provisioning API, and assignment tenants user1-user3; pass [tenant] [domain] to install one custom tenant.
set -euo pipefail

# Configuration defaults. Override image/host defaults with environment variables,
# or pass a tenant and optional domain to install one tenant instead of the samples.
DEFAULT_TENANT="${DEFAULT_TENANT:-}"
DEFAULT_TENANTS=(user1 user2 user3)
DEFAULT_DOMAIN_SUFFIX="${DEFAULT_DOMAIN_SUFFIX:-myyotta.com}"
CLUSTER_NAME="${CLUSTER_NAME:-yotta-platform}"
CLUSTER_DELETE_CONFIRMATION="delete ${CLUSTER_NAME}"
PROVISIONING_HOST="${PROVISIONING_HOST:-provisioning.myyotta.com}"
PROVISIONING_IMAGE="${PROVISIONING_IMAGE:-yotta/website-provisioning:local}"
WEBSITE_IMAGE="${WEBSITE_IMAGE:-yotta/website-app:local}"
WEBSITE_ORCHESTRATOR_IMAGE="${WEBSITE_ORCHESTRATOR_IMAGE:-yotta/website-orchestrator:local}"
PROVISIONING_CHART="${PROVISIONING_CHART:-helm/website-provisioning}"
WEBSITE_CHART="${WEBSITE_CHART:-helm/website-app}"
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
TENANTS_TO_DEPLOY=()
DOMAINS_TO_DEPLOY=()
HOSTS_TO_RESOLVE=()
script_dir=""
repo_root=""
remote=""
tenant=""
domain=""
tool=""
tenant_index=0
typed_confirmation=""
provisioning_tag=""
provisioning_repository=""

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
cd "$repo_root"

if (($# > 2)); then
  echo "Usage: ./scripts/setup-local-e2e.sh [tenant] [domain]" >&2
  exit 2
fi

if [[ -n "${1:-}" ]]; then
  tenant="$1"
  domain="${2:-${tenant}.${DEFAULT_DOMAIN_SUFFIX}}"
  TENANTS_TO_DEPLOY=("$tenant")
  DOMAINS_TO_DEPLOY=("$domain")
elif [[ -n "$DEFAULT_TENANT" ]]; then
  tenant="$DEFAULT_TENANT"
  domain="${tenant}.${DEFAULT_DOMAIN_SUFFIX}"
  TENANTS_TO_DEPLOY=("$tenant")
  DOMAINS_TO_DEPLOY=("$domain")
else
  TENANTS_TO_DEPLOY=("${DEFAULT_TENANTS[@]}")
  for tenant in "${TENANTS_TO_DEPLOY[@]}"; do
    DOMAINS_TO_DEPLOY+=("${tenant}.${DEFAULT_DOMAIN_SUFFIX}")
  done
fi

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

# The orchestrator needs a GitHub token to start the Website App Deployment workflow.
# GitHub never returns the value of a repository secret, so the token is taken, in this order, from:
#   1. ORCHESTRATOR_GITHUB_TOKEN, if already set;
#   2. the GitHub CLI login on this Mac (`gh auth token`), if it can reach this repository's workflows;
#   3. a prompt.
if [[ -z "${ORCHESTRATOR_GITHUB_TOKEN:-}" ]] && command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
  gh_token="$(gh auth token 2>/dev/null || true)"
  if [[ -n "$gh_token" ]] && GH_TOKEN="$gh_token" gh api "repos/$ORCHESTRATOR_REPOSITORY/actions/workflows" --silent 2>/dev/null; then
    echo "Using your GitHub CLI login (gh auth token) as the orchestrator's GitHub token"
    ORCHESTRATOR_GITHUB_TOKEN="$gh_token"
    export ORCHESTRATOR_GITHUB_TOKEN
  else
    echo "GitHub CLI login cannot reach $ORCHESTRATOR_REPOSITORY workflows; asking for a token instead" >&2
  fi
  unset gh_token
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

for tenant_index in "${!TENANTS_TO_DEPLOY[@]}"; do
  tenant="${TENANTS_TO_DEPLOY[$tenant_index]}"
  domain="${DOMAINS_TO_DEPLOY[$tenant_index]}"
  echo "Deploying tenant $tenant ($domain)..."
  PULL_IMAGE=false "$DEPLOY_TENANT_SCRIPT" "$tenant" "$domain" "$WEBSITE_IMAGE" "$WEBSITE_ORCHESTRATOR_IMAGE"
done

echo
echo "End-to-end local setup is ready."
echo "Provisioning API: https://${PROVISIONING_HOST}:${LOCAL_HTTPS_PORT}"
echo "Tenant websites:"
for domain in "${DOMAINS_TO_DEPLOY[@]}"; do
  echo "  https://${domain}:${LOCAL_HTTPS_PORT}"
done

HOSTS_TO_RESOLVE=("$PROVISIONING_HOST" "${DOMAINS_TO_DEPLOY[@]}")
echo
echo "For local name resolution, add this line to /etc/hosts if it is not present:"
printf '%s %s\n' "$LOCAL_HOST_IP" "${HOSTS_TO_RESOLVE[*]}"
