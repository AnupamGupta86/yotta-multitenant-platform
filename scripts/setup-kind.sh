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
DASHBOARDS_RELEASE=grafana-dashboards
DASHBOARDS_CHART=helm/monitoring/grafana-dashboards
LOGGING_RELEASE=logging
LOGGING_CHART=helm/logging-helm
KAFKA_RELEASE=kafka
KAFKA_CHART=helm/kafka-helm
WEBSITE_ORCHESTRATOR_RELEASE=website-orchestrator
WEBSITE_ORCHESTRATOR_CHART=helm/website-orchestrator-helm
PLATFORM_NAMESPACE=platform
ORCHESTRATOR_TOKEN_SECRET=orchestrator-github-token
PLATFORM_TIMEOUT=15m
WEBSITE_ORCHESTRATOR_DEPLOYMENT=website-orchestrator
# A new tag per setup run: re-using one fixed tag (":local") leaves pods started earlier on the old image,
# because an unchanged Deployment spec triggers no rollout. setup-local-e2e.sh passes its own tag in.
LOCAL_IMAGE_TAG="${LOCAL_IMAGE_TAG:-local-$(date -u +%Y%m%d%H%M%S)}"
WEBSITE_ORCHESTRATOR_IMAGE="yotta/website-orchestrator:$LOCAL_IMAGE_TAG"
FLUENTD_IMAGE="yotta/fluentd-loki:$LOCAL_IMAGE_TAG"
FLUENTD_DOCKERFILE=helm/logging-helm/image/Dockerfile
FLUENTD_BUILD_CONTEXT=helm/logging-helm/image
WEBSITE_ORCHESTRATOR_BUILD_CONTEXT=services/website-orchestrator
ROLLOUT_TIMEOUT=5m
OBSERVABILITY_UIS="https://grafana.myyotta.com:8443 https://prometheus.myyotta.com:8443 https://alertmanager.myyotta.com:8443"
NOTIFICATION_WEBHOOK_URL="${NOTIFICATION_WEBHOOK_URL:-}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cluster_name="$CLUSTER_NAME"
# shellcheck disable=SC1091 # The retry helper is resolved relative to this script at runtime.
source "$script_dir/helm-retry.sh"

for tool in "${REQUIRED_TOOLS[@]}"; do
  command -v "$tool" >/dev/null || { echo "Install $tool and retry" >&2; exit 1; }
done
# shellcheck source=scripts/check-docker.sh
source "$script_dir/check-docker.sh"   # Colima or Docker Desktop: reachable, started, sized
: "${ORCHESTRATOR_REPOSITORY:?Set ORCHESTRATOR_REPOSITORY to owner/repository}"
: "${ORCHESTRATOR_GITHUB_TOKEN:?Set ORCHESTRATOR_GITHUB_TOKEN to a fine-grained token with Actions write access}"

if ! kind get clusters | grep -qx "$cluster_name"; then
  kind create cluster --name "$cluster_name" --config kind/cluster.yaml
fi
# After a Colima or Docker restart the Kind node containers stay stopped: start them again
# (deploy-cluster-addons.sh then waits until every node is Ready)
for node in $(kind get nodes --name "$cluster_name"); do
  if [ "$(docker inspect --format '{{.State.Status}}' "$node" 2>/dev/null)" != running ]; then
    echo "Starting stopped Kind node container $node"
    docker start "$node" >/dev/null
  fi
done
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

helm upgrade --install "$DASHBOARDS_RELEASE" "$DASHBOARDS_CHART" \
  --namespace "$PROMETHEUS_NAMESPACE" --wait --timeout "$ROLLOUT_TIMEOUT"
helm upgrade --install "$LOGGING_RELEASE" "$LOGGING_CHART" \
  --namespace "$PROMETHEUS_NAMESPACE" --create-namespace \
  --set-string images.fluentd="$FLUENTD_IMAGE" \
  --wait --timeout "$PLATFORM_TIMEOUT"
helm upgrade --install "$KAFKA_RELEASE" "$KAFKA_CHART" \
  --namespace "$PLATFORM_NAMESPACE" --create-namespace \
  --wait --timeout "$PLATFORM_TIMEOUT"
# Website orchestrator: its own chart and release, next to Kafka in the platform namespace
helm upgrade --install "$WEBSITE_ORCHESTRATOR_RELEASE" "$WEBSITE_ORCHESTRATOR_CHART" \
  --namespace "$PLATFORM_NAMESPACE" \
  --set-string image="$WEBSITE_ORCHESTRATOR_IMAGE" \
  --set-string github.repository="$ORCHESTRATOR_REPOSITORY" \
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
echo "Kind platform is ready on HTTPS port 8443. Observability (add the hostnames to /etc/hosts): $OBSERVABILITY_UIS"
