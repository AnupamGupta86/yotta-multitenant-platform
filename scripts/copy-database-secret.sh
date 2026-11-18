#!/usr/bin/env bash
# Copy the platform database password into another namespace, so a service there can read it.
# Kubernetes Secrets are only readable inside their own namespace; the source of truth stays the Secret
# that helm/database-helm generated in platform-data. The password goes through stdin, never a command line.
# Usage: scripts/copy-database-secret.sh <target-namespace>
#   Env (defaults from .github/pipeline-config.env): DATABASE_NAMESPACE, DATABASE_SECRET
set -euo pipefail

target="${1:?usage: copy-database-secret.sh <target-namespace>}"
source_namespace="${DATABASE_NAMESPACE:-platform-data}"
secret="${DATABASE_SECRET:-myyotta-postgresql-secret}"
key="${DATABASE_SECRET_KEY:-POSTGRES_PASSWORD}"

if ! kubectl get secret "$secret" --namespace "$source_namespace" >/dev/null 2>&1; then
  echo "::error::Secret $source_namespace/$secret not found: deploy the platform database first (Database Deployment, or helm/database-helm)." >&2
  exit 1
fi

kubectl create namespace "$target" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl get secret "$secret" --namespace "$source_namespace" -o jsonpath="{.data.$key}" | base64 -d \
  | kubectl create secret generic "$secret" --namespace "$target" --from-file="$key=/dev/stdin" \
      --dry-run=client -o yaml \
  | kubectl label --local -f - app.kubernetes.io/managed-by=copy-database-secret -o yaml \
  | kubectl apply -f - >/dev/null
echo "Database credentials available in namespace $target (Secret $secret)"
