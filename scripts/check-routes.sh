#!/usr/bin/env bash
# Check that every Ingress route in the cluster answers, and that nothing stale is behind it.
#   - each host is called several times, so one stale pod among several shows up as a mixed result
#   - no host is claimed by two Ingresses (ingress-nginx would pick one of them)
#   - no Deployment still has pods running an image other than its current one
# Does not need /etc/hosts: requests go straight to the Kind ingress on 127.0.0.1:8443.
# Usage: ./scripts/check-routes.sh      Exit code 1 when anything is wrong.
set -euo pipefail

LOCAL_HTTPS_PORT="${LOCAL_HTTPS_PORT:-8443}"
LOCAL_HOST_IP="${LOCAL_HOST_IP:-127.0.0.1}"
ATTEMPTS=3
failed=0

path_for() {  # a page that returns 200 on each kind of host
  case "$1" in
    grafana.*) echo /login ;;
    prometheus.* | alertmanager.*) echo /-/ready ;;
    *) echo /api/v1 ;;
  esac
}

hosts="$(kubectl get ingress -A -o jsonpath='{range .items[*]}{range .spec.rules[*]}{.host}{"\n"}{end}{end}' | sed '/^$/d')"
[[ -n "$hosts" ]] || { echo "No Ingress routes found. Deploy the platform first." >&2; exit 1; }

all_ok=""   # "200 200 200 " for ATTEMPTS=3
for ((i = 1; i <= ATTEMPTS; i++)); do all_ok+="200 "; done
echo "Routes (https://<host>:$LOCAL_HTTPS_PORT, $ATTEMPTS calls each):"
for host in $(sort -u <<<"$hosts"); do
  path="$(path_for "$host")"
  codes=""
  for ((i = 1; i <= ATTEMPTS; i++)); do
    codes+="$(curl -sk -o /dev/null -w '%{http_code}' -m 5 \
      --resolve "$host:$LOCAL_HTTPS_PORT:$LOCAL_HOST_IP" "https://$host:$LOCAL_HTTPS_PORT$path" || true) "
  done
  if [ "$codes" = "$all_ok" ]; then status=ok; else status=FAIL; failed=1; fi
  printf '  %-4s %-36s %-10s %s\n' "$status" "$host" "$path" "$codes"
done

duplicates="$(sort <<<"$hosts" | uniq -d)"
if [[ -n "$duplicates" ]]; then
  failed=1
  echo "Hosts claimed by more than one Ingress (remove the extra one):"
  for host in $duplicates; do
    kubectl get ingress -A -o jsonpath="{range .items[?(@.spec.rules[0].host=='$host')]}  $host  {.metadata.namespace}/{.metadata.name}{'\n'}{end}"
  done
fi

# Pods whose image differs from their Deployment's current image (an old ReplicaSet still serving)
stale="$(kubectl get deployments -A -o json | python3 -c '
import json, subprocess, sys
for d in json.load(sys.stdin)["items"]:
    ns, name = d["metadata"]["namespace"], d["metadata"]["name"]
    want = {c["name"]: c["image"] for c in d["spec"]["template"]["spec"]["containers"]}
    selector = ",".join("%s=%s" % (k, v) for k, v in (d["spec"]["selector"].get("matchLabels") or {}).items())
    if not selector:
        continue
    pods = json.loads(subprocess.run(["kubectl", "get", "pods", "-n", ns, "-l", selector, "-o", "json"],
                                     capture_output=True, text=True, check=True).stdout)["items"]
    for p in pods:
        if p["metadata"].get("deletionTimestamp"):
            continue
        for c in p["spec"]["containers"]:
            if c["name"] in want and c["image"] != want[c["name"]]:
                print("  %s/%s: runs %s, deployment wants %s" % (ns, p["metadata"]["name"], c["image"], want[c["name"]]))
')"
if [[ -n "$stale" ]]; then
  failed=1
  echo "Pods on an old image (fix: kubectl rollout restart deployment/<name> -n <namespace>):"
  echo "$stale"
fi

if ((failed)); then
  echo "Route check FAILED" >&2
  exit 1
fi
echo "All routes answer 200, each host has one Ingress, and no pod runs an old image."
