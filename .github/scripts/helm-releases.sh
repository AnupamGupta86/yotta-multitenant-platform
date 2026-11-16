#!/usr/bin/env bash
# The Helm upgrade of each CD pipeline, defined once. The "Review changes" job runs it with HELM_MODE=diff
# and the approved deploy job with HELM_MODE=apply, so reviewers approve exactly what gets applied.
# Usage: .github/scripts/helm-releases.sh <platform|monitoring|orchestrator|provisioning|aiops>
# Needs the variables from .github/pipeline-config.env plus the ones named under each component.
set -euo pipefail

release_cmd="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/scripts/helm-release.sh"

case "${1:?usage: helm-releases.sh <platform|monitoring|orchestrator|provisioning|aiops>}" in
  platform)  # needs FLUENTD_IMAGE, GHCR_REPOSITORY
    "$release_cmd" "$PLATFORM_RELEASE_NAME" helm/platform --namespace "$PLATFORM_NAMESPACE" --create-namespace \
      --reuse-values --set-string images.fluentd="$FLUENTD_IMAGE" \
      --set-string orchestrator.repository="$GHCR_REPOSITORY" \
      --set-string orchestrator.githubToken= \
      --wait --timeout "$PLATFORM_HELM_TIMEOUT"
    ;;
  monitoring)  # needs the prometheus-community Helm repository
    "$release_cmd" "$MONITORING_RELEASE_NAME" prometheus-community/kube-prometheus-stack \
      --version "$(tr -d '[:space:]' < helm/monitoring/chart-version.txt)" \
      --namespace "$OBSERVABILITY_NAMESPACE" --create-namespace \
      --values helm/monitoring/values.yaml --wait --timeout "$PLATFORM_HELM_TIMEOUT"
    ;;
  orchestrator)  # needs IMAGE (website-orchestrator), GHCR_REPOSITORY
    "$release_cmd" "$PLATFORM_RELEASE_NAME" helm/platform --namespace "$PLATFORM_NAMESPACE" --reuse-values \
      --set-string images.websiteOrchestrator="$IMAGE" \
      --set-string orchestrator.repository="$GHCR_REPOSITORY" \
      --set-string orchestrator.githubToken= \
      --wait --timeout "$SERVICE_HELM_TIMEOUT"
    ;;
  provisioning)  # needs IMAGE (website-provisioning)
    "$release_cmd" "$WEBSITE_PROVISIONING_IMAGE" helm/website-provisioning \
      --namespace "$PLATFORM_NAMESPACE" \
      --set-string image.repository="${IMAGE%:*}" \
      --set-string image.tag="${IMAGE##*:}" \
      --wait --timeout "$SERVICE_HELM_TIMEOUT"
    ;;
  aiops)  # needs IMAGE (aiops-pod-doctor), GROK_MODEL, SMTP_HOST, SMTP_PORT
    "$release_cmd" "$AIOPS_DEPLOYMENT" aiops-pod-doctor/helm \
      --namespace "$AIOPS_NAMESPACE" \
      --set-string image.repository="${IMAGE%:*}" \
      --set-string image.tag="${IMAGE##*:}" \
      --set-string grok.existingSecret="$AIOPS_GROK_SECRET" \
      --set-string grok.model="$GROK_MODEL" \
      --set-string email.existingSecret="$AIOPS_SMTP_SECRET" \
      --set-string email.smtpHost="$SMTP_HOST" \
      --set-string email.smtpPort="$SMTP_PORT" \
      --wait --timeout "$SERVICE_HELM_TIMEOUT"
    ;;
  *) echo "unknown component: $1" >&2; exit 2 ;;
esac
