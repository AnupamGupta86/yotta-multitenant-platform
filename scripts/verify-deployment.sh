#!/usr/bin/env bash
set -euo pipefail

tenant="${1:-user1}"
domain="${2:-$tenant.localtest.me}"
mkdir -p output
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
file="output/${tenant}-${stamp}.log"
{
  echo "Tenant=$tenant Domain=$domain Timestamp=$stamp"
  kubectl get all --namespace "$tenant" -o wide
  kubectl get hpa,pdb,resourcequota,ingress --namespace "$tenant"
  echo "HTTPS smoke check:"
  curl --insecure --show-error --fail --resolve "$domain:8443:127.0.0.1" "https://$domain:8443/api/v1"
  echo
  echo "Readiness check:"
  curl --insecure --show-error --fail --resolve "$domain:8443:127.0.0.1" "https://$domain:8443/health/ready"
  echo
  echo "Tenant deployment events:"
  kubectl logs --namespace platform deployment/event-consumer --tail=40
  echo "HPA metrics:"
  kubectl top pods --namespace "$tenant" || true
} 2>&1 | tee "$file"
echo "Saved verification evidence to $file"
