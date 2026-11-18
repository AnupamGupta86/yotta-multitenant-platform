#!/usr/bin/env bash
# Store the orchestrator's GitHub token as a Kubernetes Secret, without putting it in Helm values
# or on a command line. The orchestrator uses it to start Website Deployment for each website
# request, so it must be the long-lived ORCHESTRATOR_GITHUB_TOKEN secret, not the job's GITHUB_TOKEN
# (which GitHub revokes when the job ends).
set -euo pipefail

: "${ORCHESTRATOR_GITHUB_TOKEN:?ORCHESTRATOR_GITHUB_TOKEN must be provided by the deployment job}"
: "${PLATFORM_NAMESPACE:?PLATFORM_NAMESPACE must be loaded from pipeline-config.env}"
: "${ORCHESTRATOR_TOKEN_SECRET:?ORCHESTRATOR_TOKEN_SECRET must be loaded from pipeline-config.env}"

printf '%s' "$ORCHESTRATOR_GITHUB_TOKEN" \
  | kubectl create secret generic "$ORCHESTRATOR_TOKEN_SECRET" \
      --namespace "$PLATFORM_NAMESPACE" \
      --from-file=token=/dev/stdin \
      --dry-run=client -o yaml \
  | kubectl apply -f -

kubectl rollout restart deployment/website-orchestrator --namespace "$PLATFORM_NAMESPACE"
kubectl rollout status deployment/website-orchestrator --namespace "$PLATFORM_NAMESPACE" --timeout="${ROLLOUT_TIMEOUT:-5m}"
