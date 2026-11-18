#!/usr/bin/env bash
# Safety check before Website Teardown deletes a namespace: the target must be a DNS-safe tenant name,
# must not be a platform or system namespace, and must contain a website Helm release.
# Usage: .github/scripts/check-teardown-target.sh <tenant>
set -euo pipefail

tenant="${1:?usage: check-teardown-target.sh <tenant>}"
protected="default kube-system kube-public kube-node-lease ${PLATFORM_NAMESPACE:-platform} \
${OBSERVABILITY_NAMESPACE:-observability} ${AIOPS_NAMESPACE:-aiops} ${DATABASE_NAMESPACE:-platform-data} \
infrastructure cert-manager tigera-operator calico-system calico-apiserver local-path-storage"

if [[ ! "$tenant" =~ ^[a-z][a-z0-9-]{1,30}[a-z0-9]$ ]]; then
  echo "::error::'$tenant' is not a valid tenant name" >&2
  exit 2
fi
for name in $protected; do
  if [ "$tenant" = "$name" ]; then
    echo "::error::'$tenant' is a platform or system namespace and can never be torn down" >&2
    exit 2
  fi
done
chart="$(helm list --namespace "$tenant" --filter "^${tenant}\$" --output json | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r[0]["chart"] if r else "")')"
case "$chart" in
  website-[0-9]*) echo "OK: $tenant is a tenant website (chart $chart)" ;;
  "")
    if kubectl get namespace "$tenant" >/dev/null 2>&1; then
      echo "::error::namespace $tenant exists but has no website release named $tenant; refusing to delete it" >&2
      exit 2
    fi
    echo "::error::no website named $tenant is deployed (nothing to tear down)" >&2
    exit 2
    ;;
  *) echo "::error::release $tenant uses chart $chart, not website; refusing" >&2; exit 2 ;;
esac
