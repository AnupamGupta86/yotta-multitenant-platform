#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091 # The env file is resolved relative to this script at runtime.
source "$script_dir/cluster-addons.env"

for tool in helm kubectl; do
  command -v "$tool" >/dev/null || { echo "Install $tool and retry" >&2; exit 1; }
done

helm repo add projectcalico https://docs.tigera.io/calico/charts --force-update
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx --force-update
helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/ --force-update
helm repo update projectcalico ingress-nginx metrics-server

helm upgrade --install calico-crds helm/cluster-addons/calico-crds \
  --namespace tigera-operator --create-namespace --wait --timeout 10m
helm upgrade --install calico projectcalico/tigera-operator \
  --version "$CALICO_VERSION" --namespace tigera-operator \
  --set 'installation.calicoNetwork.ipPools[0].cidr=192.168.0.0/16' \
  --force-conflicts --wait --timeout 10m
for _ in {1..60}; do
  if kubectl get daemonset calico-node --namespace calico-system >/dev/null 2>&1; then
    break
  fi
  sleep 5
done
kubectl rollout status daemonset/calico-node --namespace calico-system --timeout=10m
kubectl wait --for=condition=Ready nodes --all --timeout=10m

helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
  --version "$INGRESS_NGINX_VERSION" \
  --namespace ingress-nginx --create-namespace --wait --timeout 10m \
  --set controller.kind=DaemonSet \
  --set controller.hostPort.enabled=true \
  --set-string controller.nodeSelector.ingress-ready=true \
  --set-json 'controller.tolerations=[{"key":"node-role.kubernetes.io/control-plane","operator":"Exists","effect":"NoSchedule"}]' \
  --set controller.service.type=ClusterIP \
  --take-ownership

helm upgrade --install metrics-server metrics-server/metrics-server \
  --version "$METRICS_SERVER_VERSION" \
  --namespace kube-system --set-string 'args[0]=--kubelet-insecure-tls' \
  --take-ownership --wait --timeout 5m
kubectl rollout status deployment/metrics-server --namespace kube-system --timeout=5m

helm upgrade --install cert-manager oci://quay.io/jetstack/charts/cert-manager \
  --version "$CERT_MANAGER_VERSION" --namespace cert-manager --create-namespace \
  --set crds.enabled=true --wait --timeout 10m
kubectl wait --for=condition=Available deployment --all --namespace cert-manager --timeout=5m
