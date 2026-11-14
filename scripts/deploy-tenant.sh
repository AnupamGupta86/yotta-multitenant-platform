#!/usr/bin/env bash
set -euo pipefail

tenant="${1:?usage: deploy-tenant.sh <user1|user2|user3> <domain> <image> }"
domain="${2:?domain is required}"
image="${3:?image is required}"
event_image="${4:-yotta/event-consumer:local}"
if [[ ! "$tenant" =~ ^user[1-3]$ ]]; then
  echo "Unsupported tenant: $tenant (allowed: user1, user2, user3)" >&2
  exit 2
fi
if [[ ! "$domain" =~ ^[A-Za-z0-9.-]+$ ]]; then
  echo "Domain contains unsupported characters" >&2
  exit 2
fi
if [[ ! "$image" =~ ^[A-Za-z0-9._:/-]+$ ]]; then
  echo "Image reference contains unsupported characters" >&2
  exit 2
fi
if [[ ! "$event_image" =~ ^[A-Za-z0-9._:/-]+$ ]]; then
  echo "Event publisher image reference contains unsupported characters" >&2
  exit 2
fi

namespace="$tenant"
release="$tenant"
previous_revision=""
if helm status "$release" --namespace "$namespace" >/dev/null 2>&1; then
  previous_revision="$(helm history "$release" --namespace "$namespace" --max 1 --output json | python3 -c 'import json,sys; print(json.load(sys.stdin)[-1]["revision"])')"
fi

tag="${image##*:}"
repository="${image%:*}"
failure_rollback() {
  local status=$?
  if [[ $status -ne 0 ]]; then
    echo "Deployment failed for $tenant; restoring last successful release" >&2
    if [[ -n "$previous_revision" ]]; then
      helm rollback "$release" "$previous_revision" --namespace "$namespace" --wait --timeout 5m || true
    else
      helm uninstall "$release" --namespace "$namespace" || true
    fi
  fi
  exit "$status"
}
trap failure_rollback EXIT

if [[ "${PULL_IMAGE:-true}" == "true" ]]; then
  docker pull "$image"
  docker pull "$event_image"
fi
kind load docker-image "$image" --name yotta-platform
kind load docker-image "$event_image" --name yotta-platform
helm upgrade --install "$release" helm/website \
  --namespace "$namespace" \
  --create-namespace \
  --take-ownership \
  --set tenant.name="$tenant" \
  --set image.repository="$repository" \
  --set image.tag="$tag" \
  --set ingress.host="$domain" \
  --set database.name="$tenant" \
  --set eventPublisher.enabled=true \
  --set eventPublisher.image="$event_image" \
  --atomic --wait --timeout 10m

for attempt in {1..30}; do
  if curl --silent --show-error --insecure --fail --resolve "$domain:8443:127.0.0.1" "https://$domain:8443/health/ready" >/dev/null; then
    break
  fi
  if [[ "$attempt" == 30 ]]; then
    echo "HTTPS readiness check failed for https://$domain:8443" >&2
    exit 1
  fi
  sleep 4
done

helm test "$release" --namespace "$namespace" --logs --timeout 3m

echo "Deployment healthy: tenant=$tenant domain=$domain image=$image"
trap - EXIT
