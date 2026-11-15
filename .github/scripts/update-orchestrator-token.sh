#!/usr/bin/env bash
# Apply the Actions token as a Kubernetes Secret without putting it in Helm values or argv.
set -euo pipefail

: "${GHCR_TOKEN:?GHCR_TOKEN must be provided by the protected deployment job}"
: "${PLATFORM_NAMESPACE:?PLATFORM_NAMESPACE must be loaded from pipeline-config.env}"
: "${ORCHESTRATOR_TOKEN_SECRET:?ORCHESTRATOR_TOKEN_SECRET must be loaded from pipeline-config.env}"

printf '%s' "$GHCR_TOKEN" \
  | kubectl create secret generic "$ORCHESTRATOR_TOKEN_SECRET" \
      --namespace "$PLATFORM_NAMESPACE" \
      --from-file=token=/dev/stdin \
      --dry-run=client -o yaml \
  | kubectl apply -f -

kubectl rollout restart deployment/website-orchestrator --namespace "$PLATFORM_NAMESPACE"
kubectl rollout status deployment/website-orchestrator --namespace "$PLATFORM_NAMESPACE" --timeout="${ROLLOUT_TIMEOUT:-5m}"
