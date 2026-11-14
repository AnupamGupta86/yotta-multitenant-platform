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

helm repo add projectcalico https://docs.tigera.io/calico/charts --force-update
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx --force-update
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/ --force-update
helm repo update

helm upgrade --install calico-crds projectcalico/projectcalico.org.v3 \
  --version v3.33.0 --namespace tigera-operator --create-namespace \
  --take-ownership --wait --timeout 10m
helm upgrade --install calico projectcalico/tigera-operator \
  --version v3.33.0 --namespace tigera-operator \
  --set 'installation.calicoNetwork.ipPools[0].cidr=192.168.0.0/16' \
  --wait --timeout 10m
kubectl rollout status daemonset/calico-node --namespace calico-system --timeout=10m
kubectl wait --for=condition=Ready nodes --all --timeout=10m

helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx --create-namespace --wait --timeout 10m \
  --set controller.kind=DaemonSet \
  --set controller.hostPort.enabled=true \
  --set-string controller.nodeSelector.ingress-ready=true \
  --set-json 'controller.tolerations=[{"key":"node-role.kubernetes.io/control-plane","operator":"Exists","effect":"NoSchedule"}]' \
  --set controller.service.type=ClusterIP \
  --take-ownership

helm upgrade --install metrics-server metrics-server/metrics-server \
  --namespace kube-system --set-string 'args[0]=--kubelet-insecure-tls' \
  --take-ownership --wait --timeout 5m
kubectl rollout status deployment/metrics-server --namespace kube-system --timeout=5m

helm upgrade --install cert-manager oci://quay.io/jetstack/charts/cert-manager \
  --version v1.21.3 --namespace cert-manager --create-namespace \
  --set crds.enabled=true --wait --timeout 10m
kubectl wait --for=condition=Available deployment --all --namespace cert-manager --timeout=5m
helm upgrade --install prometheus prometheus-community/kube-prometheus-stack \
  --namespace observability --create-namespace \
  --values helm/monitoring/values.yaml --wait --timeout 15m

docker build -t yotta/fluentd-loki:local -f helm/monitoring/fluentd/Dockerfile helm/monitoring/fluentd
kind load docker-image yotta/fluentd-loki:local --name yotta-platform
docker build -t yotta/database-api:local services/database
docker build -t yotta/event-consumer:local services/event-consumer
kind load docker-image yotta/database-api:local yotta/event-consumer:local --name yotta-platform

helm upgrade --install platform helm/platform \
  --namespace platform --create-namespace --take-ownership \
  --set-string orchestrator.repository="$ORCHESTRATOR_REPOSITORY" \
  --set-string orchestrator.githubToken="$ORCHESTRATOR_GITHUB_TOKEN" \
  --set-string notification.webhookUrl="${NOTIFICATION_WEBHOOK_URL:-}" \
  --wait --timeout 15m
kubectl rollout status deployment/database-api --namespace platform --timeout=5m
kubectl rollout status deployment/event-consumer --namespace platform --timeout=5m
echo "Platform is ready. Deploy Day 0 with the Provisioning API GitHub Actions workflow."
echo "Kind platform is ready on HTTPS port 8443. Run: kubectl port-forward -n observability svc/prometheus-grafana 3000:80"
