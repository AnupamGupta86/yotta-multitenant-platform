#!/usr/bin/env bash
set -euo pipefail

for tool in docker kind kubectl helm; do
  command -v "$tool" >/dev/null || { echo "Install $tool and retry" >&2; exit 1; }
done
docker info >/dev/null 2>&1 || { echo "Start Docker Desktop before setup" >&2; exit 1; }
: "${ORCHESTRATOR_REPOSITORY:?Set ORCHESTRATOR_REPOSITORY to owner/repository}"
: "${ORCHESTRATOR_GITHUB_TOKEN:?Set ORCHESTRATOR_GITHUB_TOKEN to a fine-grained token with Actions write access}"

if ! kind get clusters | grep -qx yotta-platform; then
  kind create cluster --config kind/cluster.yaml
elif kubectl get daemonset calico-node --namespace kube-system >/dev/null 2>&1 && ! helm status calico -n tigera-operator >/dev/null 2>&1; then
  echo "This Kind cluster has Calico installed from raw manifests. Back up any demo data, then recreate the cluster to switch CNI ownership to Helm." >&2
  exit 2
fi
kubectl cluster-info --context kind-yotta-platform

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
helm repo update

./scripts/deploy-cluster-addons.sh
helm upgrade --install prometheus prometheus-community/kube-prometheus-stack \
  --namespace observability --create-namespace \
  --version "$(tr -d '[:space:]' < helm/monitoring/chart-version.txt)" \
  --values helm/monitoring/values.yaml --wait --timeout 15m

docker build -t yotta/fluentd-loki:local -f helm/monitoring/fluentd/Dockerfile helm/monitoring/fluentd
kind load docker-image yotta/fluentd-loki:local --name yotta-platform
docker build -t yotta/event-consumer:local services/event-consumer
kind load docker-image yotta/event-consumer:local --name yotta-platform

helm upgrade --install platform helm/platform \
  --namespace platform --create-namespace --take-ownership \
  --set-string orchestrator.repository="$ORCHESTRATOR_REPOSITORY" \
  --set-string orchestrator.githubToken="$ORCHESTRATOR_GITHUB_TOKEN" \
  --set-string notification.webhookUrl="${NOTIFICATION_WEBHOOK_URL:-}" \
  --wait --timeout 15m
kubectl rollout status deployment/event-consumer --namespace platform --timeout=5m
echo "Platform is ready. Deploy Day 0 with the Provisioning API GitHub Actions workflow."
echo "Kind platform is ready on HTTPS port 8443. Run: kubectl port-forward -n observability svc/prometheus-grafana 3000:80"
