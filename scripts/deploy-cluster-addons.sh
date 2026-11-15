#!/usr/bin/env bash
# Install the pinned CNI, ingress, metrics, and certificate-manager charts; run with ./scripts/deploy-cluster-addons.sh from the repository root.
set -euo pipefail

REQUIRED_TOOLS=(helm kubectl)
CALICO_CIDR='192.168.0.0/16'
CALICO_NAMESPACE=tigera-operator
CALICO_SYSTEM_NAMESPACE=calico-system
INGRESS_NAMESPACE=ingress-nginx
METRICS_NAMESPACE=kube-system
CERT_MANAGER_NAMESPACE=cert-manager
CALICO_CRDS_CHART=helm/cluster-addons/calico-crds
CALICO_REPOSITORY=projectcalico
CALICO_REPOSITORY_URL=https://docs.tigera.io/calico/charts
INGRESS_REPOSITORY=ingress-nginx
INGRESS_REPOSITORY_URL=https://kubernetes.github.io/ingress-nginx
METRICS_REPOSITORY=metrics-server
METRICS_REPOSITORY_URL=https://kubernetes-sigs.github.io/metrics-server/
CERT_MANAGER_CHART=oci://quay.io/jetstack/charts/cert-manager
CALICO_CRDS_RELEASE=calico-crds
CALICO_RELEASE=calico
INGRESS_RELEASE=ingress-nginx
METRICS_RELEASE=metrics-server
CERT_MANAGER_RELEASE=cert-manager
CALICO_NODE_WAIT_ATTEMPTS=60
CALICO_NODE_WAIT_SECONDS=5
CALICO_TIMEOUT=10m
INGRESS_TIMEOUT=10m
METRICS_TIMEOUT=5m
CERT_MANAGER_TIMEOUT=10m
ROLLOUT_TIMEOUT=5m
NODE_READY_TIMEOUT=10m

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091 # The env file is resolved relative to this script at runtime.
source "$script_dir/cluster-addons.env"
# shellcheck disable=SC1091 # The retry helper is resolved relative to this script at runtime.
source "$script_dir/helm-retry.sh"

for tool in "${REQUIRED_TOOLS[@]}"; do
  command -v "$tool" >/dev/null || { echo "Install $tool and retry" >&2; exit 1; }
done

helm_retry helm repo add "$CALICO_REPOSITORY" "$CALICO_REPOSITORY_URL" --force-update
helm_retry helm repo update "$CALICO_REPOSITORY"

helm upgrade --install "$CALICO_CRDS_RELEASE" "$CALICO_CRDS_CHART" \
  --namespace "$CALICO_NAMESPACE" --create-namespace --wait --timeout "$CALICO_TIMEOUT"
helm upgrade --install "$CALICO_RELEASE" "$CALICO_REPOSITORY/tigera-operator" \
  --version "$CALICO_VERSION" --namespace "$CALICO_NAMESPACE" \
  --set "installation.calicoNetwork.ipPools[0].cidr=$CALICO_CIDR" \
  --force-conflicts --wait --timeout "$CALICO_TIMEOUT"
for ((wait_attempt = 1; wait_attempt <= CALICO_NODE_WAIT_ATTEMPTS; wait_attempt++)); do
  if kubectl get daemonset calico-node --namespace "$CALICO_SYSTEM_NAMESPACE" >/dev/null 2>&1; then
    break
  fi
  sleep "$CALICO_NODE_WAIT_SECONDS"
done
kubectl rollout status daemonset/calico-node --namespace "$CALICO_SYSTEM_NAMESPACE" --timeout="$CALICO_TIMEOUT"
kubectl wait --for=condition=Ready nodes --all --timeout="$NODE_READY_TIMEOUT"
kubectl rollout status deployment/coredns --namespace "$METRICS_NAMESPACE" --timeout="$ROLLOUT_TIMEOUT"
kubectl rollout status deployment/local-path-provisioner --namespace local-path-storage --timeout="$ROLLOUT_TIMEOUT"

helm_retry helm repo add "$INGRESS_REPOSITORY" "$INGRESS_REPOSITORY_URL" --force-update
helm_retry helm repo add "$METRICS_REPOSITORY" "$METRICS_REPOSITORY_URL" --force-update
helm_retry helm repo update "$INGRESS_REPOSITORY" "$METRICS_REPOSITORY"

helm upgrade --install "$INGRESS_RELEASE" "$INGRESS_REPOSITORY/ingress-nginx" \
  --version "$INGRESS_NGINX_VERSION" \
  --namespace "$INGRESS_NAMESPACE" --create-namespace --wait --timeout "$INGRESS_TIMEOUT" \
  --set controller.kind=DaemonSet \
  --set controller.hostPort.enabled=true \
  --set-string controller.nodeSelector.ingress-ready=true \
  --set-json 'controller.tolerations=[{"key":"node-role.kubernetes.io/control-plane","operator":"Exists","effect":"NoSchedule"}]' \
  --set controller.service.type=ClusterIP \
  --take-ownership

helm upgrade --install "$METRICS_RELEASE" "$METRICS_REPOSITORY/metrics-server" \
  --version "$METRICS_SERVER_VERSION" \
  --namespace "$METRICS_NAMESPACE" --set-string 'args[0]=--kubelet-insecure-tls' \
  --take-ownership --wait --timeout "$METRICS_TIMEOUT"
kubectl rollout status deployment/metrics-server --namespace "$METRICS_NAMESPACE" --timeout="$ROLLOUT_TIMEOUT"

helm upgrade --install "$CERT_MANAGER_RELEASE" "$CERT_MANAGER_CHART" \
  --version "$CERT_MANAGER_VERSION" --namespace "$CERT_MANAGER_NAMESPACE" --create-namespace \
  --set crds.enabled=true --wait --timeout "$CERT_MANAGER_TIMEOUT"
kubectl wait --for=condition=Available deployment --all --namespace "$CERT_MANAGER_NAMESPACE" --timeout="$ROLLOUT_TIMEOUT"
