#!/usr/bin/env bash
# Follow website requests through the platform.
# Usage: ./scripts/trace-website.sh <tenant>            one website, every hop, one snapshot:
#                                                       API record -> Kafka events -> orchestrator -> GitHub Actions run
#                                                       -> Kubernetes (Helm, pods) -> HTTPS
#        ./scripts/trace-website.sh <tenant> --watch    the same, refreshed every 5 s until Ctrl+C (use it during a demo)
#        ./scripts/trace-website.sh --all               every website: its latest Kafka event as one status line
#        ./scripts/trace-website.sh --all --watch       the same, redrawn on every new event
# Needs kubectl and curl; gh (logged in) for the Actions hop. API_URL defaults to https://api.myyotta.com:8443.
set -uo pipefail

tenant="${1:?usage: trace-website.sh <tenant>|--all [--watch]}"
watch=false
[ "${2:-}" = "--watch" ] && watch=true
NAMESPACE="${PLATFORM_NAMESPACE:-platform}"
TOPIC="${KAFKA_TOPIC:-website-events}"

# ---- --all: one status line per website, read from the Kafka topic (Kafka tools inside the broker pod)
if [ "$tenant" = "--all" ]; then
  POD="${KAFKA_POD:-kafka-0}"
  BOOTSTRAP=localhost:19092
  READ_TIMEOUT_MS=5000
consume_args="--bootstrap-server $BOOTSTRAP --topic $TOPIC --from-beginning"
[ "$watch" = true ] || consume_args="$consume_args --timeout-ms $READ_TIMEOUT_MS"

# shellcheck disable=SC2086 # consume_args is deliberately split into words
kubectl exec -i -n "$NAMESPACE" "$POD" -- /opt/kafka/bin/kafka-console-consumer.sh $consume_args 2>/dev/null \
| WATCH="$watch" python3 -u -c '
import datetime, json, os, select, sys

STATUS = {"WebsiteProvisioningRequested": "REQUESTED", "WebsiteCreated": "LIVE",
          "WebsiteDeploymentFailed": "FAILED", "WebsiteDeletionRequested": "DELETING",
          "WebsiteDeleted": "DELETED"}
COLOUR = {"REQUESTED": "\033[33m", "LIVE": "\033[32m", "FAILED": "\033[31m", "DELETING": "\033[35m",
          "DELETED": "\033[90m"}
watch = os.environ.get("WATCH") == "true" and sys.stdout.isatty()
latest, count = {}, {}

def table():
    if watch:
        sys.stdout.write("\033[H\033[2J")   # clear the screen
        print("Kafka topic website-events, updated %s (Ctrl+C to stop)\n" % datetime.datetime.now().strftime("%H:%M:%S"))
    print("%-10s %-10s %-28s %-7s %s" % ("TENANT", "STATUS", "DOMAIN", "EVENTS", "LAST EVENT"))
    for tenant, e in sorted(latest.items()):
        status = STATUS.get(e.get("event_type"), e.get("event_type") or "?")
        when = e.get("occurred_at") or e.get("requested_at") or ""
        line = "%-10s %-10s %-28s %-7s %s" % (tenant, status, e.get("domain", ""), count[tenant], when)
        print(COLOUR.get(status, "") + line + "\033[0m" if watch else line)
    if not latest:
        print("(no events yet)")
    sys.stdout.flush()

while True:
    line = sys.stdin.readline()   # one line at a time, so new events show immediately
    if not line:
        break
    try:
        event = json.loads(line)
    except ValueError:
        continue
    tenant = event.get("tenant", "?")
    latest[tenant] = event
    count[tenant] = count.get(tenant, 0) + 1
    # Redraw once the backlog read at start-up is done, then on every new event
    if watch and not select.select([sys.stdin], [], [], 0.2)[0]:
        table()

table()
'
  exit 0
fi
API_URL="${API_URL:-https://api.myyotta.com:8443}"
KAFKA_POD="${KAFKA_POD:-kafka-0}"
HTTPS_PORT="${HTTPS_PORT:-8443}"
case "$tenant" in *[!a-z0-9-]*) echo "tenant must be a DNS label (a-z, 0-9, -)" >&2; exit 2 ;; esac

hop() { printf '\n\033[1m%s\033[0m\n' "$1"; }
ok() { printf '  \033[32m%s\033[0m\n' "$1"; }
wait_() { printf '  \033[33m%s\033[0m\n' "$1"; }
bad() { printf '  \033[31m%s\033[0m\n' "$1"; }

snapshot() {
  printf '\033[1mRequest path for %s\033[0m   (%s)\n' "$tenant" "$(date '+%H:%M:%S')"
  echo "  POST /api/v1/websites -> 202 -> Kafka -> orchestrator -> Website Deployment (approve) -> namespace $tenant"

  # 1. API: the website record in the platform database
  hop "1. Hosting Platform API (record in myyotta_platform)"
  record="$(curl -ksS --max-time 5 "$API_URL/api/v1/websites" 2>/dev/null | TENANT="$tenant" python3 -c '
import json, os, sys
try:
    sites = json.load(sys.stdin)["websites"]
except Exception:
    print("ERROR"); sys.exit()
w = next((s for s in sites if s["tenant"] == os.environ["TENANT"]), None)
if not w:
    print("NONE")
else:
    last = w.get("lastDeployment") or {}
    print("%s|%s|%s|%s|%s" % (w["status"], w["domain"], w["ownerEmail"], last.get("status", "-"), last.get("imageTag", "-")))')"
  domain="$tenant.myyotta.com"
  case "$record" in
    ERROR) bad "API not reachable at $API_URL" ;;
    NONE)  wait_ "no website '$tenant' in the API (not requested yet, or already deleted)" ;;
    *) IFS='|' read -r status domain owner last_status last_tag <<EOF_REC
$record
EOF_REC
       line="status $status, domain $domain, owner $owner, last deployment $last_status ($last_tag)"
       case "$status" in ACTIVE) ok "$line" ;; FAILED) bad "$line" ;; *) wait_ "$line" ;; esac ;;
  esac

  # 2. Kafka: every event for this tenant, in order
  hop "2. Kafka topic $TOPIC"
  events="$(kubectl exec -i -n "$NAMESPACE" "$KAFKA_POD" -- /opt/kafka/bin/kafka-console-consumer.sh \
      --bootstrap-server localhost:19092 --topic "$TOPIC" --from-beginning --timeout-ms 4000 2>/dev/null \
    | TENANT="$tenant" python3 -c '
import json, os, sys
for line in sys.stdin:
    try:
        e = json.loads(line)
    except ValueError:
        continue
    if e.get("tenant") == os.environ["TENANT"]:
        print("%s %-30s %s" % ((e.get("occurred_at") or e.get("requested_at") or "")[:19], e.get("event_type"), e.get("event_id")))')"
  if [ -n "$events" ]; then printf '%s\n' "$events" | sed 's/^/  /'; else wait_ "no events for $tenant"; fi
  event_ids="$(printf '%s\n' "$events" | awk 'NF {print $NF}' | paste -sd'|' -)"

  # 3. Orchestrator: what it did with those events (dispatch, notification, e-mail)
  hop "3. Website orchestrator (last actions)"
  pattern="tenant=$tenant( |$)"
  [ -n "$event_ids" ] && pattern="$pattern|$event_ids"
  logs="$(kubectl logs -n "$NAMESPACE" deploy/website-orchestrator --since=24h 2>/dev/null \
    | grep -E "$pattern" | grep -E "WEBSITE_EVENT|Started|E-mail|NOTIFICATION|Notification|failed|Error" | tail -n 8)"
  if [ -n "$logs" ]; then printf '%s\n' "$logs" | cut -c1-170 | sed 's/^/  /'; else wait_ "no orchestrator log lines for $tenant in the last 24 h"; fi

  # 4. GitHub Actions: the deployment / teardown runs for this tenant. Runs named "<workflow> · <tenant>" match
  # directly; older runs (named only "Website Deployment") are matched by time: the orchestrator starts the
  # workflow within seconds of the request event, so a run created up to 3 minutes after it belongs to it.
  hop "4. GitHub Actions"
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    runs="$( { gh run list --workflow website_cd.yml --limit 100 --json displayTitle,status,conclusion,createdAt,url 2>/dev/null \
               | sed 's/^/D /'; echo; \
               gh run list --workflow website_teardown.yml --limit 100 --json displayTitle,status,conclusion,createdAt,url 2>/dev/null \
               | sed 's/^/T /'; echo; } \
      | TENANT="$tenant" EVENTS="$events" python3 -c '
import datetime, json, os, sys
tenant, found = os.environ["TENANT"], []
def utc(text):
    return datetime.datetime.fromisoformat(text.replace("Z", "+00:00")[:19])
starts = {"D": [], "T": []}          # request times that start each workflow
for line in os.environ.get("EVENTS", "").splitlines():
    parts = line.split()
    if len(parts) >= 2 and parts[1] in ("WebsiteProvisioningRequested", "WebsiteDeletionRequested"):
        try:
            starts["D" if parts[1] == "WebsiteProvisioningRequested" else "T"].append(utc(parts[0]))
        except ValueError:
            pass
for line in sys.stdin:
    kind, _, body = line.partition(" ")
    if kind not in starts or not body.strip():
        continue
    for run in json.loads(body):
        title, created = run["displayTitle"], utc(run["createdAt"])
        if title.endswith("· " + tenant):
            how = ""
        elif "·" not in title and any(0 <= (created - t).total_seconds() <= 180 for t in starts[kind]):
            how = "  (matched by request time)"
        else:
            continue
        state = run["status"] if run["status"] != "completed" else run["conclusion"] or "-"
        found.append((run["createdAt"][:19], "%s %s: %s %s%s" % (run["createdAt"][:19], title, state, run["url"], how)))
for _, text in sorted(found, reverse=True)[:5]:
    print(text)')"
    if [ -n "$runs" ]; then printf '%s\n' "$runs" | sed 's/^/  /'
      case "$runs" in *": waiting "*|*": queued "*) wait_ "-> waiting: open the run and approve it ('Review deployments')" ;; esac
    else wait_ "no Website Deployment / Teardown run for $tenant (deployed by a script, e.g. setup-local-e2e.sh, or older than the 100 newest runs)"; fi
  else
    wait_ "gh not installed or not logged in: skip (gh auth login)"
  fi

  # 5. Kubernetes: the tenant's own release and pods
  hop "5. Kubernetes namespace $tenant"
  if kubectl get namespace "$tenant" >/dev/null 2>&1; then
    helm_line="$(helm list -n "$tenant" --filter "^$tenant\$" 2>/dev/null | tail -n +2)"
    if [ -n "$helm_line" ]; then ok "helm: $(printf '%s' "$helm_line" | awk '{print "revision " $3 ", " $8 ", chart " $9}')"; else wait_ "helm release not installed yet"; fi
    kubectl get pods,ingress -n "$tenant" --no-headers 2>/dev/null | sed 's/^/  /'
  else
    wait_ "namespace $tenant does not exist (not deployed yet, or torn down)"
  fi

  # 6. HTTPS through the Ingress
  hop "6. HTTPS https://$domain:$HTTPS_PORT/health/ready"
  code="$(curl -ks -o /dev/null -w '%{http_code}' --max-time 5 --resolve "$domain:$HTTPS_PORT:127.0.0.1" "https://$domain:$HTTPS_PORT/health/ready" 2>/dev/null)"
  case "$code" in 200) ok "200 ready" ;; 000) wait_ "no answer yet" ;; *) bad "HTTP $code" ;; esac
}

if [ "$watch" = true ]; then
  while true; do
    out="$(snapshot 2>&1)"
    printf '\033[H\033[2J%s\n\n(refreshes every 5 s, Ctrl+C to stop)\n' "$out"
    sleep 5
  done
else
  snapshot
fi
