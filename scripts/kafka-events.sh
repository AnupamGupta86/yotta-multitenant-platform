#!/usr/bin/env bash
# Show the website lifecycle events on Kafka as one status line per tenant.
# Usage: ./scripts/kafka-events.sh            table once, then exit
#        ./scripts/kafka-events.sh --watch    keep running; the table redraws on every new event (Ctrl+C to stop)
# Reads the topic from the beginning with the Kafka tools inside the broker pod; nothing to install.
set -euo pipefail

NAMESPACE="${PLATFORM_NAMESPACE:-platform}"
POD="${KAFKA_POD:-kafka-0}"
TOPIC="${KAFKA_TOPIC:-website-events}"
BOOTSTRAP=localhost:19092
READ_TIMEOUT_MS=5000

watch=false
[ "${1:-}" = "--watch" ] && watch=true

consume_args="--bootstrap-server $BOOTSTRAP --topic $TOPIC --from-beginning"
[ "$watch" = true ] || consume_args="$consume_args --timeout-ms $READ_TIMEOUT_MS"

# shellcheck disable=SC2086 # consume_args is deliberately split into words
kubectl exec -i -n "$NAMESPACE" "$POD" -- /opt/kafka/bin/kafka-console-consumer.sh $consume_args 2>/dev/null \
| WATCH="$watch" python3 -u -c '
import datetime, json, os, select, sys

STATUS = {"WebsiteProvisioningRequested": "REQUESTED", "WebsiteCreated": "LIVE",
          "WebsiteDeploymentFailed": "FAILED"}
COLOUR = {"REQUESTED": "\033[33m", "LIVE": "\033[32m", "FAILED": "\033[31m"}
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
