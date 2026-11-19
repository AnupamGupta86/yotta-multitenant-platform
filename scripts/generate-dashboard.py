#!/usr/bin/env python3
"""Generate the Grafana dashboard "Yotta Website Platform" (helm/monitoring/grafana-dashboards/files/website-dashboard.json).

Edit the panels here, then run from the repository root:  python3 scripts/generate-dashboard.py
Filters: Namespace and Service (container). Rows: overview, pods and health, CPU, memory, network,
website traffic, HTTP errors per service, Kafka and website events, the platform database, logs.
"""
import json

NS = 'namespace=~"$namespace"'
SEL = 'namespace=~"$namespace", container=~"$service"'
PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}
panels = []
y = 0
pid = 0

def nid():
    global pid
    pid += 1
    return pid

def row(title):
    global y
    panels.append({"id": nid(), "type": "row", "title": title, "collapsed": False,
                   "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "panels": []})
    y += 1

def ts(title, targets, unit="short", x=0, w=12, h=8, desc="", extra=None):
    p = {"id": nid(), "title": title, "type": "timeseries", "datasource": PROM, "description": desc,
         "gridPos": {"h": h, "w": w, "x": x, "y": y},
         "fieldConfig": {"defaults": {"unit": unit, "decimals": 2, "custom": {"fillOpacity": 10, "lineWidth": 1}}, "overrides": []},
         "options": {"legend": {"displayMode": "table", "placement": "bottom", "showLegend": True, "calcs": ["lastNotNull", "max"]},
                     "tooltip": {"mode": "multi", "sort": "desc"}},
         "targets": [{"refId": chr(65 + i), "expr": e, "legendFormat": l, "datasource": PROM} for i, (e, l) in enumerate(targets)]}
    if extra:
        p["fieldConfig"]["defaults"].update(extra)
    panels.append(p)

def stat(title, expr, unit="short", x=0, w=4, thresholds=None, desc=""):
    steps = thresholds or [{"color": "green", "value": None}]
    panels.append({"id": nid(), "title": title, "type": "stat", "datasource": PROM, "description": desc,
                   "gridPos": {"h": 4, "w": w, "x": x, "y": y},
                   "fieldConfig": {"defaults": {"unit": unit, "decimals": 0 if unit == "short" else 1,
                                                "thresholds": {"mode": "absolute", "steps": steps},
                                                "noValue": "0"}, "overrides": []},
                   "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                               "colorMode": "background", "graphMode": "area", "textMode": "auto"},
                   "targets": [{"refId": "A", "expr": expr, "datasource": PROM}]})

def bad_if_above(limit, warn=None):
    steps = [{"color": "green", "value": None}]
    if warn is not None:
        steps.append({"color": "orange", "value": warn})
    steps.append({"color": "red", "value": limit})
    return steps

# ---------------- Overview
row("Overview (filtered by Namespace and Service)")
stat("Running containers", f'count(kube_pod_container_info{{{SEL}}})', x=0,
     desc="Containers of the selected services that exist right now (one per pod for these services).")
stat("Not ready", f'sum(1 - kube_pod_container_status_ready{{{SEL}}})', x=4, thresholds=bad_if_above(1),
     desc="Containers failing their readiness probe. Should be 0.")
stat("Waiting (CrashLoop, ImagePull…)", f'count(kube_pod_container_status_waiting_reason{{{SEL}}} == 1)', x=8,
     thresholds=bad_if_above(1), desc="Containers stuck in a waiting state; see 'Why containers are not running' below.")
stat("Restarts (last 1h)", f'sum(increase(kube_pod_container_status_restarts_total{{{SEL}}}[1h]))', x=12,
     thresholds=bad_if_above(3, 1))
stat("CPU used / requested", f'100 * sum(rate(container_cpu_usage_seconds_total{{{SEL}, container!=""}}[5m])) / sum(kube_pod_container_resource_requests{{{SEL}, resource="cpu"}})',
     unit="percent", x=16, thresholds=bad_if_above(200, 100), desc="Above 100% the pods use more than they reserve.")
stat("Memory used / limit", f'100 * sum(container_memory_working_set_bytes{{{SEL}, container!=""}}) / sum(kube_pod_container_resource_limits{{{SEL}, resource="memory"}})',
     unit="percent", x=20, thresholds=bad_if_above(90, 75), desc="Near 100% a container is about to be OOM-killed.")
y += 4

# ---------------- Pods and health
row("Pods and health")
ts("Ready pods per service", [(f'sum by (namespace, container) (kube_pod_container_status_ready{{{SEL}}})', "{{namespace}} / {{container}}")],
   x=0, desc="Pod count over time; drops show restarts or failed rollouts.")
ts("Container restarts (15m)", [(f'sum by (namespace, pod) (increase(kube_pod_container_status_restarts_total{{{SEL}}}[15m])) > 0', "{{namespace}} / {{pod}}")],
   x=12, desc="Only pods that restarted are shown.")
y += 8
panels.append({"id": nid(), "title": "Why containers are not running (waiting / last termination)", "type": "table", "datasource": PROM,
               "description": "CrashLoopBackOff, ImagePullBackOff, OOMKilled, Error... Empty = healthy.",
               "gridPos": {"h": 7, "w": 24, "x": 0, "y": y},
               "options": {"showHeader": True},
               "transformations": [{"id": "merge", "options": {}},
                                   {"id": "organize", "options": {"excludeByName": {"Time": True, "Value #A": True, "Value #B": True},
                                                                  "renameByName": {"reason": "Reason"}}}],
               "targets": [
                   {"refId": "A", "instant": True, "format": "table", "datasource": PROM,
                    "expr": f'kube_pod_container_status_waiting_reason{{{SEL}}} == 1'},
                   {"refId": "B", "instant": True, "format": "table", "datasource": PROM,
                    "expr": f'(kube_pod_container_status_last_terminated_reason{{{SEL}}} == 1) and on (namespace, pod, container) (increase(kube_pod_container_status_restarts_total{{{SEL}}}[1h]) > 0)'}]})
y += 7

# ---------------- CPU
row("CPU")
ts("CPU usage by pod (cores)", [(f'sum by (namespace, pod) (rate(container_cpu_usage_seconds_total{{{SEL}, container!=""}}[5m]))', "{{namespace}} / {{pod}}")],
   unit="none", x=0, extra={"decimals": 3})
ts("CPU usage % of limit", [(f'100 * sum by (namespace, pod) (rate(container_cpu_usage_seconds_total{{{SEL}, container!=""}}[5m])) / sum by (namespace, pod) (kube_pod_container_resource_limits{{{SEL}, resource="cpu"}})', "{{namespace}} / {{pod}}")],
   unit="percent", x=12, desc="Close to 100% the container is throttled.")
y += 8
ts("CPU throttling (% of periods)", [(f'100 * sum by (namespace, pod) (rate(container_cpu_cfs_throttled_periods_total{{{SEL}, container!=""}}[5m])) / sum by (namespace, pod) (rate(container_cpu_cfs_periods_total{{{SEL}, container!=""}}[5m]))', "{{namespace}} / {{pod}}")],
   unit="percent", x=0, desc="High throttling = CPU limit too low: slow responses, failing probes.")
ts("Website replicas: current vs desired (HPA)", [
    (f'kube_horizontalpodautoscaler_status_current_replicas{{{NS}}}', "{{namespace}} current"),
    (f'kube_horizontalpodautoscaler_status_desired_replicas{{{NS}}}', "{{namespace}} desired")], x=12)
y += 8

# ---------------- Memory
row("Memory")
ts("Memory working set by pod", [(f'sum by (namespace, pod) (container_memory_working_set_bytes{{{SEL}, container!=""}})', "{{namespace}} / {{pod}}")],
   unit="bytes", x=0, desc="What the OOM killer compares against the limit.")
ts("Memory % of limit", [(f'100 * sum by (namespace, pod) (container_memory_working_set_bytes{{{SEL}, container!=""}}) / sum by (namespace, pod) (kube_pod_container_resource_limits{{{SEL}, resource="memory"}})', "{{namespace}} / {{pod}}")],
   unit="percent", x=12, desc="At 100% the container is OOM-killed.")
y += 8

# ---------------- Network
POD_OF_SERVICE = f'* on (namespace, pod) group_left () max by (namespace, pod) (kube_pod_container_info{{{SEL}}})'
row("Network")
ts("Network received by pod", [(f'sum by (namespace, pod) (rate(container_network_receive_bytes_total{{{NS}}}[5m])) {POD_OF_SERVICE}', "{{namespace}} / {{pod}}")],
   unit="Bps", x=0)
ts("Network sent by pod", [(f'sum by (namespace, pod) (rate(container_network_transmit_bytes_total{{{NS}}}[5m])) {POD_OF_SERVICE}', "{{namespace}} / {{pod}}")],
   unit="Bps", x=12)
y += 8

# ---------------- Websites (app metrics)
TEN = 'tenant=~"$namespace"'
row("Website traffic (tenant websites)")
ts("HTTP request rate by tenant", [(f'sum by (tenant) (rate(yotta_http_requests_total{{{TEN}}}[5m]))', "{{tenant}}")], unit="reqps", x=0)
ts("HTTP request rate by status code", [(f'sum by (tenant, status) (rate(yotta_http_requests_total{{{TEN}}}[5m]))', "{{tenant}} {{status}}")], unit="reqps", x=12)
y += 8
ts("Successful responses (%)", [(f'100 * sum by (tenant) (rate(yotta_http_requests_total{{{TEN}, status=~"2.."}}[5m])) / sum by (tenant) (rate(yotta_http_requests_total{{{TEN}}}[5m]))', "{{tenant}}")],
   unit="percent", x=0)
ts("HTTP request rate by endpoint", [(f'sum by (tenant, endpoint) (rate(yotta_http_requests_total{{{TEN}}}[5m]))', "{{tenant}} {{endpoint}}")], unit="reqps", x=12)
y += 8

# ---------------- HTTP errors per service (measured at ingress-nginx: websites, hosting-platform API, UIs)
ING = 'nginx_ingress_controller_requests{namespace=~"$namespace"'
BY = "sum by (namespace, service)"
ING_DESC = ("From ingress-nginx: every request that comes in through the Ingress (tenant websites, the hosting-platform "
            "API, Grafana/Prometheus). Lines are namespace/service. Filtered by Namespace only.")
row("HTTP errors per service (4XX / 5XX, at the ingress)")
stat("4XX responses in time range", f'round(sum(increase({ING}, status=~"4.."}}[$__range])))', x=0, w=6,
     thresholds=bad_if_above(100, 1), desc="Client errors: 400 bad request, 404 not found, 409 conflict, 422 invalid input...")
stat("5XX responses in time range", f'round(sum(increase({ING}, status=~"5.."}}[$__range])))', x=6, w=6,
     thresholds=bad_if_above(1), desc="Server errors: 500, 502 bad gateway (pod down), 503 unavailable, 504 timeout.")
stat("4XX rate (% of requests)", f'100 * sum(rate({ING}, status=~"4.."}}[5m])) / sum(rate({ING}}}[5m]))', unit="percent",
     x=12, w=6, thresholds=bad_if_above(20, 5), desc=ING_DESC)
stat("5XX rate (% of requests)", f'100 * sum(rate({ING}, status=~"5.."}}[5m])) / sum(rate({ING}}}[5m]))', unit="percent",
     x=18, w=6, thresholds=bad_if_above(5, 1), desc=ING_DESC)
y += 4
ts("4XX responses per minute by service", [(f'60 * {BY} (rate({ING}, status=~"4.."}}[5m]))', "{{namespace}}/{{service}}")],
   x=0, desc=ING_DESC)
ts("5XX responses per minute by service", [(f'60 * {BY} (rate({ING}, status=~"5.."}}[5m]))', "{{namespace}}/{{service}}")],
   x=12, desc=ING_DESC)
y += 8
ts("4XX and 5XX by service and status code",
   [(f'60 * sum by (namespace, service, status) (rate({ING}, status=~"[45].."}}[5m]))', "{{namespace}}/{{service}} {{status}}")],
   x=0, desc="Per minute. " + ING_DESC)
ts("Error rate by service (%)",
   [(f'100 * {BY} (rate({ING}, status=~"4.."}}[5m])) / {BY} (rate({ING}}}[5m]))', "{{namespace}}/{{service}} 4XX"),
    (f'100 * {BY} (rate({ING}, status=~"5.."}}[5m])) / {BY} (rate({ING}}}[5m]))', "{{namespace}}/{{service}} 5XX")],
   unit="percent", x=12, desc="Share of each service's requests that failed. " + ING_DESC)
y += 8

# ---------------- Kafka and website events
ORCH = r'{namespace="platform", container="website-orchestrator"} |= "WEBSITE_EVENT" | json | line_format "{{.log}}" | regexp `event_type=(?P<event_type>\S+) tenant=(?P<tenant>\S+) domain=(?P<domain>\S+)`'
row("Kafka and website events")
stat("Kafka brokers up", 'sum(kafka_brokers)', x=0, thresholds=[{"color": "red", "value": None}, {"color": "green", "value": 1}],
     desc="From kafka-exporter. 0 = Kafka down or exporter cannot reach it.")
stat("Messages in website-events", 'sum(kafka_topic_partition_current_offset{topic="website-events"})', x=4,
     desc="All website events ever written to the topic.")
stat("Unprocessed events (orchestrator lag)", 'sum(kafka_consumergroup_lag{topic="website-events"})', x=8,
     thresholds=bad_if_above(5, 1), desc="Events in the queue the orchestrator has not handled yet. Should return to 0.")
for i, (label, etype, colour) in enumerate([("Requested", "WebsiteProvisioningRequested", "blue"),
                                             ("Created (live)", "WebsiteCreated", "green"),
                                             ("Deployment failed", "WebsiteDeploymentFailed", "red")]):
    panels.append({"id": nid(), "title": f"{label} in time range", "type": "stat", "datasource": LOKI,
                   "gridPos": {"h": 4, "w": 4, "x": 12 + 4 * i, "y": y},
                   "fieldConfig": {"defaults": {"unit": "short", "noValue": "0", "color": {"mode": "fixed", "fixedColor": colour}}, "overrides": []},
                   "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "colorMode": "value", "graphMode": "none"},
                   "targets": [{"refId": "A", "datasource": LOKI, "queryType": "instant",
                                "expr": f'sum(count_over_time({ORCH} | event_type="{etype}" [$__range]))'}]})
y += 4
ts("Kafka messages per minute by topic", [('60 * sum by (topic) (rate(kafka_topic_partition_current_offset{topic!~"__.*"}[5m]))', "{{topic}}")], x=0)
ts("Consumer lag (queue backlog)", [('sum by (consumergroup, topic) (kafka_consumergroup_lag)', "{{consumergroup}} / {{topic}}")], x=12,
   desc="Rising lag = the orchestrator is down or failing on a message (see its logs).")
y += 8
for x, title, by, flt in [(0, "Website events by type", "event_type", ""), (12, "Website events by tenant", "tenant, event_type", ' | tenant=~"$namespace"')]:
    panels.append({"id": nid(), "title": title, "type": "timeseries", "datasource": LOKI,
                   "gridPos": {"h": 8, "w": 12, "x": x, "y": y},
                   "fieldConfig": {"defaults": {"unit": "short", "custom": {"drawStyle": "bars", "fillOpacity": 70, "stacking": {"mode": "normal"}}}, "overrides": [
                       {"matcher": {"id": "byRegexp", "options": ".*Failed.*"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]},
                       {"matcher": {"id": "byRegexp", "options": ".*Created.*"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "green"}}]},
                       {"matcher": {"id": "byRegexp", "options": ".*Requested.*"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "blue"}}]}]},
                   "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True}, "tooltip": {"mode": "multi"}},
                   "targets": [{"refId": "A", "datasource": LOKI, "legendFormat": "{{tenant}} {{event_type}}" if "tenant" in by else "{{event_type}}",
                                "expr": f"sum by ({by}) (count_over_time({ORCH}{flt} [$__interval]))"}]})
y += 8
panels.append({"id": nid(), "title": "Website event log (newest first)", "type": "logs", "datasource": LOKI,
               "gridPos": {"h": 8, "w": 24, "x": 0, "y": y},
               "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending"},
               "targets": [{"refId": "A", "datasource": LOKI,
                            "expr": '{namespace="platform", container="website-orchestrator"} |= "WEBSITE_EVENT" | json | line_format "{{.log}}"'}]})
y += 8

# ---------------- Platform database
row("Platform database")
ts("Ready PostgreSQL replicas", [(f'sum by (namespace, statefulset) (kube_statefulset_status_replicas_ready{{{NS}, statefulset=~".*postgres.*"}})', "{{namespace}} / {{statefulset}}")], x=0)
ts("PostgreSQL volume usage", [(f'sum by (namespace, persistentvolumeclaim) (kubelet_volume_stats_used_bytes{{{NS}}})', "{{namespace}} / {{persistentvolumeclaim}}")],
   unit="bytes", x=12, desc="Empty if the storage driver does not report volume stats (Kind local-path).")
y += 8

# ---------------- Logs
row("Logs (Loki)")
panels.append({"id": nid(), "title": "Error lines per minute", "type": "timeseries", "datasource": LOKI,
               "gridPos": {"h": 7, "w": 24, "x": 0, "y": y},
               "fieldConfig": {"defaults": {"unit": "short", "custom": {"drawStyle": "bars", "fillOpacity": 60}}, "overrides": []},
               "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True}, "tooltip": {"mode": "multi"}},
               "targets": [{"refId": "A", "datasource": LOKI, "legendFormat": "{{namespace}} / {{container}}",
                            "expr": 'sum by (namespace, container) (count_over_time({namespace=~"$namespace", container=~"$service"} |~ "(?i)(error|exception|traceback|fatal)" [1m]))'}]})
y += 7
panels.append({"id": nid(), "title": "Logs of the selected services", "type": "logs", "datasource": LOKI,
               "gridPos": {"h": 12, "w": 24, "x": 0, "y": y},
               "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "enableLogDetails": True},
               "targets": [{"refId": "A", "datasource": LOKI,
                            "expr": '{namespace=~"$namespace", container=~"$service"} | json | line_format "{{.kubernetes_pod_name}} {{.log}}"'}]})

templating = {"list": [
    {"name": "namespace", "label": "Namespace (service area / website)", "type": "query", "datasource": PROM,
     "query": {"query": "label_values(kube_pod_info, namespace)", "refId": "namespace"},
     "definition": "label_values(kube_pod_info, namespace)",
     "regex": "/^(?!kube-|calico-|tigera-|cert-manager|infrastructure|local-path|default$).*/",
     "multi": True, "includeAll": True, "refresh": 2, "sort": 1,
     "current": {"selected": True, "text": ["All"], "value": ["$__all"]}},
    {"name": "service", "label": "Service (container)", "type": "query", "datasource": PROM,
     "query": {"query": 'label_values(kube_pod_container_info{namespace=~"$namespace"}, container)', "refId": "service"},
     "definition": 'label_values(kube_pod_container_info{namespace=~"$namespace"}, container)',
     "multi": True, "includeAll": True, "refresh": 2, "sort": 1,
     "current": {"selected": True, "text": ["All"], "value": ["$__all"]}},
]}

dashboard = {
    "title": "Yotta Website Platform", "uid": "yotta-websites", "schemaVersion": 39, "version": 4,
    "description": "Filter by Namespace (platform, aiops, observability, user1, user2...) and Service (container). "
                   "Overview -> pods -> CPU -> memory -> network -> website traffic -> HTTP errors per service -> Kafka and website events -> platform database -> logs.",
    "refresh": "15s", "time": {"from": "now-30m", "to": "now"}, "tags": ["website-platform", "debugging"],
    "timezone": "browser", "editable": True, "graphTooltip": 1,
    "templating": templating, "panels": panels,
}
with open("helm/monitoring/grafana-dashboards/files/website-dashboard.json", "w") as output:
    json.dump(dashboard, output, indent=2)
print(len(panels), "panels; last y =", y)
