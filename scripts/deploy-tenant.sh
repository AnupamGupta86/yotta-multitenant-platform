#!/usr/bin/env bash
# Deploy and verify one tenant Helm release, rolling back on failure; run with ./scripts/deploy-tenant.sh <tenant> <domain> <image> [orchestrator-image] [correlation-id].
set -euo pipefail

DEFAULT_ORCHESTRATOR_IMAGE=yotta/website-orchestrator:local
DEFAULT_CLUSTER_NAME=yotta-platform
TENANT_CHART=helm/website-app
HEALTH_PATH=/health/ready
LOCAL_HTTPS_PORT=8443
LOCAL_HOST_IP=127.0.0.1
PULL_IMAGE="${PULL_IMAGE:-true}"
HELM_TIMEOUT=10m
# Rollback demo: SIMULATE_FAILURE=true deploys a release whose readiness probe can never pass,
# so the rollout fails, Helm rolls back to the last good revision and CD publishes WebsiteDeploymentFailed.
SIMULATE_FAILURE="${SIMULATE_FAILURE:-false}"
SIMULATED_FAILURE_TIMEOUT=2m
ROLLBACK_TIMEOUT=5m
HELM_TEST_TIMEOUT=3m
HELM_ROLLBACK_OPTION=(--atomic)
READINESS_ATTEMPTS=30
READINESS_RETRY_SECONDS=4
REQUIRED_TOOLS=(docker kind helm curl python3)

tenant="${1:?usage: deploy-tenant.sh <tenant> <domain> <image> [event-image] [correlation-id]}"
domain="${2:?domain is required}"
image="${3:?image is required}"
orchestrator_image="${4:-$DEFAULT_ORCHESTRATOR_IMAGE}"
correlation_id="${5:-}"
namespace="$tenant"
release="$tenant"
previous_revision=""
tag="${image##*:}"
repository="${image%:*}"
cluster_name="${CLUSTER_NAME:-$DEFAULT_CLUSTER_NAME}"

for tool in "${REQUIRED_TOOLS[@]}"; do
  command -v "$tool" >/dev/null || { echo "Install $tool and retry" >&2; exit 1; }
done

if helm upgrade --help 2>&1 | grep -q -- '--rollback-on-failure'; then
  HELM_ROLLBACK_OPTION=(--rollback-on-failure)
fi

if [[ ! "$tenant" =~ ^[a-z][a-z0-9-]{1,30}[a-z0-9]$ ]]; then
  echo "Tenant must be a DNS-safe name between 3 and 32 characters" >&2
  exit 2
fi
if [[ ! "$domain" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$ ]]; then
  echo "Domain must contain valid DNS labels" >&2
  exit 2
fi
if [[ ! "$image" =~ ^[A-Za-z0-9._:/-]+$ ]]; then
  echo "Image reference contains unsupported characters" >&2
  exit 2
fi
if [[ ! "$orchestrator_image" =~ ^[A-Za-z0-9._:/-]+$ ]]; then
  echo "Orchestrator image reference contains unsupported characters" >&2
  exit 2
fi

if helm status "$release" --namespace "$namespace" >/dev/null 2>&1; then
  previous_revision="$(helm history "$release" --namespace "$namespace" --max 1 --output json | python3 -c 'import json,sys; print(json.load(sys.stdin)[-1]["revision"])')"
fi

failure_rollback() {
  local status=$?
  if [[ $status -ne 0 ]]; then
    echo "Deployment failed for $tenant; restoring last successful release" >&2
    if [[ -n "$previous_revision" ]]; then
      helm rollback "$release" "$previous_revision" --namespace "$namespace" --wait --timeout "$ROLLBACK_TIMEOUT" || true
    else
      helm uninstall "$release" --namespace "$namespace" || true
    fi
  fi
  exit "$status"
}
trap failure_rollback EXIT

if [[ "$PULL_IMAGE" == "true" ]]; then
  # Images from the registry are multi-platform: load only the nodes' platform
  "$(dirname "$0")/load-image-into-kind.sh" "$image" "$cluster_name"
  "$(dirname "$0")/load-image-into-kind.sh" "$orchestrator_image" "$cluster_name"
else
  # Locally built images have a single platform and load as they are
  kind load docker-image "$image" --name "$cluster_name"
  kind load docker-image "$orchestrator_image" --name "$cluster_name"
fi
extra_helm_args=()
if [[ "$SIMULATE_FAILURE" == "true" ]]; then
  echo "SIMULATE_FAILURE=true: deploying a broken readiness probe to demonstrate automatic rollback" >&2
  extra_helm_args=(--set probes.readiness.path=/simulated-failure)
  HELM_TIMEOUT="$SIMULATED_FAILURE_TIMEOUT"
fi

helm upgrade --install "$release" "$TENANT_CHART" \
  --server-side=false \
  --namespace "$namespace" \
  --create-namespace \
  --take-ownership \
  --set tenant.name="$tenant" \
  --set image.repository="$repository" \
  --set image.tag="$tag" \
  --set ingress.host="$domain" \
  --set database.name="$tenant" \
  --set eventPublisher.enabled=true \
  --set eventPublisher.image="$orchestrator_image" \
  --set eventPublisher.correlationId="$correlation_id" \
  ${extra_helm_args[@]+"${extra_helm_args[@]}"} \
  "${HELM_ROLLBACK_OPTION[@]}" --wait --timeout "$HELM_TIMEOUT"

for ((attempt = 1; attempt <= READINESS_ATTEMPTS; attempt++)); do
  if curl --silent --show-error --insecure --fail --resolve "$domain:$LOCAL_HTTPS_PORT:$LOCAL_HOST_IP" "https://$domain:$LOCAL_HTTPS_PORT$HEALTH_PATH" >/dev/null; then
    break
  fi
  if ((attempt == READINESS_ATTEMPTS)); then
    echo "HTTPS readiness check failed for https://$domain:$LOCAL_HTTPS_PORT" >&2
    exit 1
  fi
  sleep "$READINESS_RETRY_SECONDS"
done

helm test "$release" --namespace "$namespace" --logs --timeout "$HELM_TEST_TIMEOUT"

echo "Deployment healthy: tenant=$tenant domain=$domain image=$image"
trap - EXIT
