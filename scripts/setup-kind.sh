#!/usr/bin/env bash
# Create or update the Kind platform cluster, add-ons, monitoring, logging, Kafka and the website orchestrator;
# run with ./scripts/setup-kind.sh after exporting the orchestrator repository and token.
# The Helm releases are installed with .github/scripts/helm-releases.sh, the same definitions the CD pipelines use,
# and the names, namespaces and timeouts come from .github/pipeline-config.env.
set -euo pipefail

DEFAULT_CLUSTER_NAME=yotta-platform
CLUSTER_NAME="${CLUSTER_NAME:-$DEFAULT_CLUSTER_NAME}"
REQUIRED_TOOLS=(docker kind kubectl helm)
ADDONS_SCRIPT=./scripts/deploy-cluster-addons.sh
HELM_RELEASES=./.github/scripts/helm-releases.sh
PIPELINE_CONFIG=.github/pipeline-config.env
PROMETHEUS_REPOSITORY=prometheus-community
PROMETHEUS_REPOSITORY_URL=https://prometheus-community.github.io/helm-charts
# A new tag per setup run: re-using one fixed tag (":local") leaves pods started earlier on the old image,
# because an unchanged Deployment spec triggers no rollout. setup-local-e2e.sh passes its own tag in.
LOCAL_IMAGE_TAG="${LOCAL_IMAGE_TAG:-local-$(date -u +%Y%m%d%H%M%S)}"
# LOCAL_* names: .github/pipeline-config.env (loaded below) has its own WEBSITE_ORCHESTRATOR_IMAGE (the GHCR image name)
LOCAL_ORCHESTRATOR_IMAGE="yotta/website-orchestrator:$LOCAL_IMAGE_TAG"
LOCAL_FLUENTD_IMAGE="yotta/fluentd-loki:$LOCAL_IMAGE_TAG"
FLUENTD_DOCKERFILE=helm/logging-helm/image/Dockerfile
FLUENTD_BUILD_CONTEXT=helm/logging-helm/image
WEBSITE_ORCHESTRATOR_BUILD_CONTEXT=services/website-orchestrator
OBSERVABILITY_UIS="https://grafana.myyotta.com:8443 https://prometheus.myyotta.com:8443 https://alertmanager.myyotta.com:8443"
NOTIFICATION_WEBHOOK_URL="${NOTIFICATION_WEBHOOK_URL:-}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$script_dir/.."   # relative chart paths below and in helm-releases.sh start at the repository root
cluster_name="$CLUSTER_NAME"
# Release names, namespaces, secret names and timeouts shared with the CD pipelines
set -a
# shellcheck source=.github/pipeline-config.env
source "$PIPELINE_CONFIG"
set +a
export HELM_MODE=apply
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
# Monitoring first: its ServiceMonitor CRD lets the kafka release add the kafka-exporter ServiceMonitor
"$HELM_RELEASES" monitoring          # prometheus (kube-prometheus-stack) + grafana-dashboards

docker build -t "$LOCAL_FLUENTD_IMAGE" -f "$FLUENTD_DOCKERFILE" "$FLUENTD_BUILD_CONTEXT"
kind load docker-image "$LOCAL_FLUENTD_IMAGE" --name "$cluster_name"
docker build -t "$LOCAL_ORCHESTRATOR_IMAGE" "$WEBSITE_ORCHESTRATOR_BUILD_CONTEXT"
kind load docker-image "$LOCAL_ORCHESTRATOR_IMAGE" --name "$cluster_name"

FLUENTD_IMAGE="$LOCAL_FLUENTD_IMAGE" "$HELM_RELEASES" logging            # Loki + Fluentd (observability)
"$HELM_RELEASES" kafka                                            # Kafka + kafka-exporter (platform)
IMAGE="$LOCAL_ORCHESTRATOR_IMAGE" GHCR_REPOSITORY="$ORCHESTRATOR_REPOSITORY" \
  NOTIFICATION_WEBHOOK_URL="$NOTIFICATION_WEBHOOK_URL" "$HELM_RELEASES" orchestrator
printf '%s' "$ORCHESTRATOR_GITHUB_TOKEN" \
  | kubectl create secret generic "$ORCHESTRATOR_TOKEN_SECRET" \
      --namespace "$PLATFORM_NAMESPACE" --from-file=token=/dev/stdin \
      --dry-run=client -o yaml \
  | kubectl apply -f -
# Optional: e-mail every website event. Export SMTP_USERNAME, SMTP_PASSWORD (e.g. a Gmail app password) and
# NOTIFY_EMAIL_TO before running this script; the values go to the Secret through files, never arguments.
if [ -n "${SMTP_USERNAME:-}" ] && [ -n "${SMTP_PASSWORD:-}" ] && [ -n "${NOTIFY_EMAIL_TO:-}" ]; then
  smtp_dir="$(mktemp -d)"
  (umask 077
   printf '%s' "$SMTP_USERNAME" > "$smtp_dir/username"
   printf '%s' "$SMTP_PASSWORD" > "$smtp_dir/password"
   printf '%s' "$NOTIFY_EMAIL_TO" > "$smtp_dir/to")
  kubectl create secret generic "$ORCHESTRATOR_SMTP_SECRET" --namespace "$PLATFORM_NAMESPACE" \
    --from-file=username="$smtp_dir/username" --from-file=password="$smtp_dir/password" \
    --from-file=to="$smtp_dir/to" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  rm -rf "$smtp_dir"
  echo "Website event e-mails go to $NOTIFY_EMAIL_TO"
else
  echo "Website event e-mails are off (export SMTP_USERNAME, SMTP_PASSWORD and NOTIFY_EMAIL_TO to turn them on)"
fi
kubectl rollout restart "deployment/$WEBSITE_ORCHESTRATOR_DEPLOYMENT" --namespace "$PLATFORM_NAMESPACE"
kubectl rollout status "deployment/$WEBSITE_ORCHESTRATOR_DEPLOYMENT" --namespace "$PLATFORM_NAMESPACE" --timeout="$ROLLOUT_TIMEOUT"
echo "Platform is ready. Deploy Day 0 with the Hosting Platform API GitHub Actions workflow."
echo "Kind platform is ready on HTTPS port 8443. Observability (add the hostnames to /etc/hosts): $OBSERVABILITY_UIS"
