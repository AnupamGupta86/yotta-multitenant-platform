#!/usr/bin/env bash
# Collect proof-of-work evidence from the running Kind cluster into docs/proof/<UTC timestamp>/.
# One text file per assignment item; see docs/proof-of-work.md for what each file shows.
#
#   ./scripts/collect-proof.sh                      # everything, 120 s load test on user1
#   LOAD_SECONDS=0 ./scripts/collect-proof.sh       # skip the load test
#   TENANTS="user1 user2 user3 user4" ./scripts/collect-proof.sh
#
# Needs kubectl, helm, curl, python3 on the Mac that owns the cluster; gh (optional) for CI/CD runs.
set -uo pipefail   # no -e: one missing piece must not stop the rest of the evidence

TENANTS="${TENANTS:-user1 user2 user3}"
DOMAIN_SUFFIX="${DOMAIN_SUFFIX:-myyotta.com}"
HTTPS_PORT="${HTTPS_PORT:-8443}"
LOAD_TENANT="${LOAD_TENANT:-user1}"
LOAD_SECONDS="${LOAD_SECONDS:-120}"
LOAD_WORKERS="${LOAD_WORKERS:-40}"
PLATFORM_NAMESPACE=platform
OBSERVABILITY_NAMESPACE=observability
KAFKA_POD=kafka-0
KAFKA_PORT=19092
KAFKA_TOPIC=website-events
PROMETHEUS_SERVICE=prometheus-kube-prometheus-prometheus:9090
GRAFANA_SERVICE=prometheus-grafana:80
LOKI_SERVICE=loki:3100

out="docs/proof/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$out"

# run "<title>" command...  -> prints a header and the command's output (errors included)
run() {
  local title="$1"; shift
  printf '\n### %s\n$ %s\n' "$title" "$*"
  "$@" 2>&1
}

prom_query() {  # Prometheus instant query through the API server proxy (no port-forward needed)
  local query
  query="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$1")"
  kubectl get --raw "/api/v1/namespaces/$OBSERVABILITY_NAMESPACE/services/$PROMETHEUS_SERVICE/proxy/api/v1/query?query=$query" |
    python3 -c 'import json, sys
for r in json.load(sys.stdin)["data"]["result"]:
    print(r["metric"], r["value"][1])'
}

echo "Writing evidence to $out/"

# ---------------------------------------------------------------- 1. Multi-tenant websites
{
  run "Cluster nodes" kubectl get nodes -o wide
  run "Helm releases (all namespaces)" helm list --all-namespaces
  for tenant in $TENANTS; do
    domain="$tenant.$DOMAIN_SUFFIX"
    run "$tenant: workloads, services, ingress" kubectl get deploy,statefulset,svc,ingress -n "$tenant"
    run "$tenant: HPA, PDB, quota, limits, network policies" kubectl get hpa,pdb,resourcequota,limitrange,networkpolicy -n "$tenant"
    run "$tenant: TLS certificate" kubectl get certificate -n "$tenant"
    run "$tenant: HTTPS /api/v1" curl -ksS --resolve "$domain:$HTTPS_PORT:127.0.0.1" "https://$domain:$HTTPS_PORT/api/v1"
    run "$tenant: HTTPS /health/ready (checks its own database)" curl -ksS --resolve "$domain:$HTTPS_PORT:127.0.0.1" "https://$domain:$HTTPS_PORT/health/ready"
  done
  # Isolation: the first tenant's app must NOT reach the second tenant's database
  # shellcheck disable=SC2086  # split the tenant list into words on purpose
  set -- $TENANTS
  if [[ $# -ge 2 ]]; then
    run "Isolation: $1 app -> $2-postgres.$2:5432 (expected: blocked)" \
      kubectl exec -n "$1" deploy/"$1"-app -- python -c "
import socket
try:
    socket.create_connection(('$2-postgres.$2.svc.cluster.local', 5432), timeout=4)
    print('REACHABLE - isolation NOT working')
except OSError as error:
    print('BLOCKED as expected:', error)"
  fi
} > "$out/1-multi-tenant.txt"

# ---------------------------------------------------------------- 2. Scaling under load
{
  run "HPA before load" kubectl get hpa -n "$LOAD_TENANT"
  if (( LOAD_SECONDS > 0 )); then
    echo; echo "### Load test: $LOAD_WORKERS workers for ${LOAD_SECONDS}s on $LOAD_TENANT (HPA sampled every 15 s)"
    python3 scripts/load-test.py --tenant "$LOAD_TENANT" --seconds "$LOAD_SECONDS" --workers "$LOAD_WORKERS" > "$out/load-test-output.txt" 2>&1 &
    load_pid=$!
    while kill -0 "$load_pid" 2>/dev/null; do
      echo "--- $(date -u +%H:%M:%S)"
      kubectl get hpa -n "$LOAD_TENANT" --no-headers 2>&1
      sleep 15
    done
    cat "$out/load-test-output.txt"
    echo "--- 60 s after the load (scale-down is deliberately slower)"
    sleep 60
  fi
  run "HPA after load" kubectl get hpa -n "$LOAD_TENANT"
  run "HPA scaling events" kubectl describe hpa -n "$LOAD_TENANT"
  run "Pods" kubectl get pods -n "$LOAD_TENANT" -o wide
} > "$out/2-scaling.txt"

# ---------------------------------------------------------------- 3. CI/CD with rollback and events
{
  if command -v gh >/dev/null; then
    for workflow in website_app_ci.yml website_provisioning_ci.yml website_orchestrator_ci.yml platform_ci.yml \
                    platform_cd.yml website_orchestrator_cd.yml website_provisioning_cd.yml website_app_cd.yml; do
      run "Recent runs: $workflow" gh run list --workflow "$workflow" --limit 5
    done
  else
    echo "gh CLI not installed: add screenshots of the Actions tab instead"
  fi
  for tenant in $TENANTS; do
    run "$tenant: Helm revisions (a rollback shows as a new revision 'Rollback to N')" helm history "$tenant" -n "$tenant"
  done
} > "$out/3-cicd-rollback.txt"

# ---------------------------------------------------------------- 4. Kafka integration
{
  run "Kafka pod" kubectl get pod "$KAFKA_POD" -n "$PLATFORM_NAMESPACE" -o wide
  run "Topics" kubectl exec -n "$PLATFORM_NAMESPACE" "$KAFKA_POD" -- \
    /opt/kafka/bin/kafka-topics.sh --bootstrap-server "localhost:$KAFKA_PORT" --describe --topic "$KAFKA_TOPIC"
  run "Consumer groups and lag (0 = every event was handled)" kubectl exec -n "$PLATFORM_NAMESPACE" "$KAFKA_POD" -- \
    /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server "localhost:$KAFKA_PORT" --describe --all-groups
  run "All events on $KAFKA_TOPIC (requests, WebsiteCreated, WebsiteDeploymentFailed)" kubectl exec -n "$PLATFORM_NAMESPACE" "$KAFKA_POD" -- \
    /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server "localhost:$KAFKA_PORT" --topic "$KAFKA_TOPIC" \
    --from-beginning --timeout-ms 10000
  run "Orchestrator log (consumes events, dispatches deployments, notifications)" \
    kubectl logs -n "$PLATFORM_NAMESPACE" deploy/website-orchestrator --tail=60
} > "$out/4-kafka.txt"

# ---------------------------------------------------------------- 5. Observability
{
  run "Monitoring and logging pods" kubectl get pods -n "$OBSERVABILITY_NAMESPACE"
  run "Grafana dashboards (ConfigMaps)" kubectl get configmap -A -l grafana_dashboard
  echo; echo "### Prometheus: website request rate per tenant"
  prom_query 'sum by (tenant) (rate(yotta_http_requests_total[5m]))' 2>&1
  echo; echo "### Prometheus: website replicas per tenant (HPA)"
  prom_query 'kube_horizontalpodautoscaler_status_current_replicas' 2>&1
  echo; echo "### Prometheus: scrape targets up"
  prom_query 'sum by (job) (up)' 2>&1
  run "Grafana health" kubectl get --raw "/api/v1/namespaces/$OBSERVABILITY_NAMESPACE/services/$GRAFANA_SERVICE/proxy/api/health"
  run "Loki ready" kubectl get --raw "/api/v1/namespaces/$OBSERVABILITY_NAMESPACE/services/$LOKI_SERVICE/proxy/ready"
} > "$out/5-observability.txt"

echo "Done. Evidence files:"
ls -1 "$out"
echo "Add screenshots to $out/ as described in docs/proof-of-work.md."
