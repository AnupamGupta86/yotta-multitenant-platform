#!/usr/bin/env bash
# Collect tenant Kubernetes, HTTPS, website-orchestrator, and HPA evidence into output/; run with ./scripts/verify-deployment.sh [tenant] [domain].
set -euo pipefail

DEFAULT_TENANT=user1
DEFAULT_DOMAIN_SUFFIX=myyotta.com
OUTPUT_DIRECTORY=output
TIMESTAMP_FORMAT=%Y%m%dT%H%M%SZ
LOCAL_HTTPS_PORT=8443
LOCAL_HOST_IP=127.0.0.1
WEBSITE_ORCHESTRATOR_NAMESPACE=platform
WEBSITE_ORCHESTRATOR_DEPLOYMENT=website-orchestrator
EVENT_LOG_LINES=40

tenant="${1:-$DEFAULT_TENANT}"
domain="${2:-$tenant.$DEFAULT_DOMAIN_SUFFIX}"
mkdir -p "$OUTPUT_DIRECTORY"
stamp="$(date -u +"$TIMESTAMP_FORMAT")"
file="$OUTPUT_DIRECTORY/${tenant}-${stamp}.log"
{
  echo "Tenant=$tenant Domain=$domain Timestamp=$stamp"
  kubectl get all --namespace "$tenant" -o wide
  kubectl get hpa,pdb,resourcequota,ingress --namespace "$tenant"
  echo "HTTPS smoke check:"
  curl --insecure --show-error --fail --resolve "$domain:$LOCAL_HTTPS_PORT:$LOCAL_HOST_IP" "https://$domain:$LOCAL_HTTPS_PORT/api/v1"
  echo
  echo "Readiness check:"
  curl --insecure --show-error --fail --resolve "$domain:$LOCAL_HTTPS_PORT:$LOCAL_HOST_IP" "https://$domain:$LOCAL_HTTPS_PORT/health/ready"
  echo
  echo "Tenant deployment events:"
  kubectl logs --namespace "$WEBSITE_ORCHESTRATOR_NAMESPACE" "deployment/$WEBSITE_ORCHESTRATOR_DEPLOYMENT" --tail="$EVENT_LOG_LINES"
  echo "HPA metrics:"
  kubectl top pods --namespace "$tenant" || true
} 2>&1 | tee "$file"
echo "Saved verification evidence to $file"
