#!/usr/bin/env bash
# One-time move from the old single "platform" chart (Kafka, Loki, Fluentd, ClusterIssuer, dashboard, and before that
# the orchestrator) to the separate charts kafka-helm, logging-helm, cluster-addons/cluster-issuer and
# monitoring/grafana-dashboards. The new releases create the same resources, so the old release must go first.
# HELM_MODE=diff only lists what would be removed. Kafka's data survives: its PVC (data-kafka-0) belongs to the
# StatefulSet, not to the Helm release, and the new kafka StatefulSet reuses it.
# Usage: LEGACY_PLATFORM_RELEASE_NAME=platform PLATFORM_NAMESPACE=platform remove-legacy-platform-release.sh
set -euo pipefail

release="${LEGACY_PLATFORM_RELEASE_NAME:-platform}"
namespace="${PLATFORM_NAMESPACE:-platform}"

if ! helm status "$release" --namespace "$namespace" >/dev/null 2>&1; then
  echo "No legacy '$release' release in namespace $namespace: nothing to move."
  exit 0
fi

resources="$(helm get manifest "$release" --namespace "$namespace" \
  | awk '/^kind:/ {kind=$2} /^  name:/ && kind != "" {print kind "/" $2; kind=""}' | sort)"

if [ "${HELM_MODE:-apply}" = diff ]; then
  echo "::warning::The deploy job will uninstall the legacy '$release' release (one-time move to the split charts)."
  echo "Resources it removes before the new releases recreate them:"
  printf '%s\n' "$resources" | sed 's/^/  /'
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    {
      echo "### One-time move: legacy \`$release\` release is uninstalled"
      echo "Recreated by the kafka, logging, cluster-issuer and grafana-dashboards releases. Kafka data (PVC) is kept."
      echo '```'
      printf '%s\n' "$resources"
      echo '```'
    } >> "$GITHUB_STEP_SUMMARY"
  fi
  exit 0
fi

echo "Uninstalling the legacy '$release' release (Kafka is briefly unavailable; producers retry):"
printf '%s\n' "$resources" | sed 's/^/  /'
helm uninstall "$release" --namespace "$namespace" --wait --timeout 5m
