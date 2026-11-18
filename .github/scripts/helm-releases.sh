#!/usr/bin/env bash
# The Helm upgrade of each CD pipeline, defined once. The "Review changes" job runs it with HELM_MODE=diff
# and the approved deploy job with HELM_MODE=apply, so reviewers approve exactly what gets applied.
# Usage: .github/scripts/helm-releases.sh <kafka|logging|monitoring|orchestrator|hosting-platform|aiops|database>
# Needs the variables from .github/pipeline-config.env plus the ones named under each component.
# Every release works as a first install or an upgrade: upgrade --install, --create-namespace, and
# --reset-then-reuse-values (keeps values set earlier, e.g. a webhook URL, but picks up new chart defaults).
set -euo pipefail

release_cmd="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/scripts/helm-release.sh"

case "${1:?usage: helm-releases.sh <kafka|logging|monitoring|orchestrator|hosting-platform|aiops|database>}" in
  kafka)  # Kafka + kafka-exporter (namespace platform)
    "$release_cmd" "$KAFKA_RELEASE_NAME" helm/kafka-helm --namespace "$PLATFORM_NAMESPACE" --create-namespace \
      --reset-then-reuse-values --wait --timeout "$PLATFORM_HELM_TIMEOUT"
    ;;
  logging)  # needs FLUENTD_IMAGE (Loki + Fluentd, namespace observability)
    "$release_cmd" "$LOGGING_RELEASE_NAME" helm/logging-helm --namespace "$OBSERVABILITY_NAMESPACE" --create-namespace \
      --reset-then-reuse-values --set-string images.fluentd="$FLUENTD_IMAGE" \
      --wait --timeout "$PLATFORM_HELM_TIMEOUT"
    ;;
  monitoring)  # needs the prometheus-community Helm repository; Prometheus + Grafana, then the Grafana dashboards
    "$release_cmd" "$MONITORING_RELEASE_NAME" prometheus-community/kube-prometheus-stack \
      --version "$(tr -d '[:space:]' < helm/monitoring/chart-version.txt)" \
      --namespace "$OBSERVABILITY_NAMESPACE" --create-namespace \
      --values helm/monitoring/values.yaml --wait --timeout "$PLATFORM_HELM_TIMEOUT"
    "$release_cmd" "$GRAFANA_DASHBOARDS_RELEASE_NAME" helm/monitoring/grafana-dashboards \
      --namespace "$OBSERVABILITY_NAMESPACE" --reset-then-reuse-values --wait --timeout "$SERVICE_HELM_TIMEOUT"
    ;;
  orchestrator)  # needs IMAGE (website-orchestrator), GHCR_REPOSITORY; its own release since the chart split
    "$release_cmd" "$WEBSITE_ORCHESTRATOR_RELEASE_NAME" helm/website-orchestrator-helm \
      --namespace "$PLATFORM_NAMESPACE" --create-namespace --reset-then-reuse-values \
      --set-string image="$IMAGE" \
      --set-string github.repository="$GHCR_REPOSITORY" \
      --wait --timeout "$SERVICE_HELM_TIMEOUT"
    ;;
  hosting-platform)  # needs IMAGE (hosting-platform)
    "$release_cmd" "$HOSTING_PLATFORM_IMAGE" helm/hosting-platform-helm \
      --namespace "$PLATFORM_NAMESPACE" --create-namespace \
      --set-string image.repository="${IMAGE%:*}" \
      --set-string image.tag="${IMAGE##*:}" \
      --wait --timeout "$SERVICE_HELM_TIMEOUT"
    ;;
  aiops)  # needs IMAGE (aiops-pod-doctor), GROK_MODEL, SMTP_HOST, SMTP_PORT
    "$release_cmd" "$AIOPS_DEPLOYMENT" aiops-pod-doctor/aiops-pod-doctor-helm \
      --namespace "$AIOPS_NAMESPACE" --create-namespace \
      --set-string image.repository="${IMAGE%:*}" \
      --set-string image.tag="${IMAGE##*:}" \
      --set-string grok.existingSecret="$AIOPS_GROK_SECRET" \
      --set-string grok.model="$GROK_MODEL" \
      --set-string email.existingSecret="$AIOPS_SMTP_SECRET" \
      --set-string email.smtpHost="$SMTP_HOST" \
      --set-string email.smtpPort="$SMTP_PORT" \
      --wait --timeout "$SERVICE_HELM_TIMEOUT"
    ;;
  database)  # needs IMAGE (myyotta-postgresql); every name comes from the DATABASE_* defaults
    "$release_cmd" "$DATABASE_RELEASE_NAME" helm/database-helm \
      --namespace "$DATABASE_NAMESPACE" --create-namespace \
      --set-string image.repository="${IMAGE%:*}" \
      --set-string image.tag="${IMAGE##*:}" \
      --set-string database.name="$DATABASE_NAME" \
      --set-string database.user="$DATABASE_USER" \
      --set-string service.name="$DATABASE_SERVICE_NAME" \
      --set-string secret.name="$DATABASE_SECRET" \
      --wait --timeout "$SERVICE_HELM_TIMEOUT"
    ;;
  *) echo "unknown component: $1" >&2; exit 2 ;;
esac
