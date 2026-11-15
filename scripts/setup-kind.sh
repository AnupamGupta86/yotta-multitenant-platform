#!/usr/bin/env bash
# Create or update the Kind platform cluster, add-ons, monitoring, and core services; run with ./scripts/setup-kind.sh after exporting the orchestrator repository and token.
set -euo pipefail

DEFAULT_CLUSTER_NAME=yotta-platform
CLUSTER_NAME="${CLUSTER_NAME:-$DEFAULT_CLUSTER_NAME}"
REQUIRED_TOOLS=(docker kind kubectl helm)
ADDONS_SCRIPT=./scripts/deploy-cluster-addons.sh
PROMETHEUS_REPOSITORY=prometheus-community
PROMETHEUS_REPOSITORY_URL=https://prometheus-community.github.io/helm-charts
PROMETHEUS_CHART_VERSION_FILE=helm/monitoring/chart-version.txt
PROMETHEUS_VALUES_FILE=helm/monitoring/values.yaml
PROMETHEUS_RELEASE=prometheus
PROMETHEUS_CHART=kube-prometheus-stack
PROMETHEUS_NAMESPACE=observability
PROMETHEUS_TIMEOUT=15m
PLATFORM_RELEASE=platform
PLATFORM_CHART=helm/platform
PLATFORM_NAMESPACE=platform
ORCHESTRATOR_TOKEN_SECRET=orchestrator-github-token
PLATFORM_TIMEOUT=15m
WEBSITE_ORCHESTRATOR_DEPLOYMENT=website-orchestrator
WEBSITE_ORCHESTRATOR_IMAGE=yotta/website-orchestrator:local
FLUENTD_IMAGE=yotta/fluentd-loki:local
FLUENTD_DOCKERFILE=helm/monitoring/fluentd/Dockerfile
FLUENTD_BUILD_CONTEXT=helm/monitoring/fluentd
WEBSITE_ORCHESTRATOR_BUILD_CONTEXT=services/website-orchestrator
ROLLOUT_TIMEOUT=5m
GRAFANA_NAMESPACE=observability
GRAFANA_SERVICE=prometheus-grafana
GRAFANA_LOCAL_PORT=3000
GRAFANA_SERVICE_PORT=80
NOTIFICATION_WEBHOOK_URL="${NOTIFICATION_WEBHOOK_URL:-}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cluster_name="$CLUSTER_NAME"
# shellcheck disable=SC1091 # The retry helper is resolved relative to this script at runtime.
source "$script_dir/helm-retry.sh"

for tool in "${REQUIRED_TOOLS[@]}"; do
  command -v "$tool" >/dev/null || { echo "Install $tool and retry" >&2; exit 1; }
done
docker info >/dev/null 2>&1 || { echo "Start Docker Desktop before setup" >&2; exit 1; }
: "${ORCHESTRATOR_REPOSITORY:?Set ORCHESTRATOR_REPOSITORY to owner/repository}"
: "${ORCHESTRATOR_GITHUB_TOKEN:?Set ORCHESTRATOR_GITHUB_TOKEN to a fine-grained token with Actions write access}"

if ! kind get clusters | grep -qx "$cluster_name"; then
  kind create cluster --name "$cluster_name" --config kind/cluster.yaml
elif kubectl get daemonset calico-node --namespace kube-system >/dev/null 2>&1 && ! helm status calico -n tigera-operator >/dev/null 2>&1; then
  echo "This Kind cluster has Calico installed from raw manifests. Back up any demo data, then recreate the cluster to switch CNI ownership to Helm." >&2
  exit 2
fi
kubectl cluster-info --context "kind-$cluster_name"

"$ADDONS_SCRIPT"
helm_retry helm repo add "$PROMETHEUS_REPOSITORY" "$PROMETHEUS_REPOSITORY_URL" --force-update
helm_retry helm repo update "$PROMETHEUS_REPOSITORY"
helm upgrade --install "$PROMETHEUS_RELEASE" "$PROMETHEUS_REPOSITORY/$PROMETHEUS_CHART" \
  --namespace "$PROMETHEUS_NAMESPACE" --create-namespace \
  --version "$(tr -d '[:space:]' < "$PROMETHEUS_CHART_VERSION_FILE")" \
  --values "$PROMETHEUS_VALUES_FILE" --wait --timeout "$PROMETHEUS_TIMEOUT"

docker build -t "$FLUENTD_IMAGE" -f "$FLUENTD_DOCKERFILE" "$FLUENTD_BUILD_CONTEXT"
kind load docker-image "$FLUENTD_IMAGE" --name "$cluster_name"
docker build -t "$WEBSITE_ORCHESTRATOR_IMAGE" "$WEBSITE_ORCHESTRATOR_BUILD_CONTEXT"
kind load docker-image "$WEBSITE_ORCHESTRATOR_IMAGE" --name "$cluster_name"

helm upgrade --install "$PLATFORM_RELEASE" "$PLATFORM_CHART" \
  --namespace "$PLATFORM_NAMESPACE" --create-namespace --take-ownership \
  --set-string orchestrator.repository="$ORCHESTRATOR_REPOSITORY" \
  --set-string notification.webhookUrl="$NOTIFICATION_WEBHOOK_URL" \
  --wait --timeout "$PLATFORM_TIMEOUT"
printf '%s' "$ORCHESTRATOR_GITHUB_TOKEN" \
  | kubectl create secret generic "$ORCHESTRATOR_TOKEN_SECRET" \
      --namespace "$PLATFORM_NAMESPACE" --from-file=token=/dev/stdin \
      --dry-run=client -o yaml \
  | kubectl apply -f -
kubectl rollout restart deployment/website-orchestrator --namespace "$PLATFORM_NAMESPACE"
kubectl rollout status "deployment/$WEBSITE_ORCHESTRATOR_DEPLOYMENT" --namespace "$PLATFORM_NAMESPACE" --timeout="$ROLLOUT_TIMEOUT"
echo "Platform is ready. Deploy Day 0 with the Provisioning API GitHub Actions workflow."
echo "Kind platform is ready on HTTPS port 8443. Run: kubectl port-forward -n $GRAFANA_NAMESPACE svc/$GRAFANA_SERVICE $GRAFANA_LOCAL_PORT:$GRAFANA_SERVICE_PORT"
