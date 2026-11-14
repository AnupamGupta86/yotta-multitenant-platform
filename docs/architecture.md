# Architecture

## Runtime topology

```text
GitHub-hosted CI
  ├─ Python tests, Ruff, Bandit, pip-audit, Gitleaks
  ├─ Dockerfile lint, image build, Trivy and runtime probe
  └─ publish website-api image + SBOM/provenance to GHCR
          │
          ▼
Self-hosted Mac runner ── Kind cluster
  ├─ user1 namespace: website API + private PostgreSQL StatefulSet
  ├─ user2 namespace: website API + private PostgreSQL StatefulSet
  ├─ user3 namespace: website API + private PostgreSQL StatefulSet
  ├─ ingress-nginx + cert-manager (one TLS Ingress/Certificate per host)
  ├─ metrics-server + Prometheus Operator + Grafana
  └─ Fluentd DaemonSet ──> Loki ──> Grafana

Helm platform release: provisioning API + private PostgreSQL, database API + private PostgreSQL, Kafka KRaft broker + Python event consumer, Loki, Fluentd, ClusterIssuer, Grafana dashboard
```

Helm owns all Kubernetes resources. Each website namespace owns its deployment, database service, password Secret, HPA, PDB, quota, LimitRange, network policy, Ingress, and ServiceMonitor. The platform release owns the provisioning and database APIs, their separate database StatefulSets/Secrets, Kafka and its consumer, and the local logging configuration. Network policy admits website HTTP only from ingress-nginx/observability and database traffic only from that namespace's own API pods. Tenant PostgreSQL instances are never shared.

## Request and event flow

Ingress terminates TLS and forwards host-matched traffic to a ClusterIP service. The website API exposes health/version endpoints, a local CPU workload endpoint for load testing, and Prometheus request counters at `/metrics`. Readiness verifies the tenant's PostgreSQL instance. After a Helm upgrade and HTTPS readiness check succeed, `helm test` creates a temporary publisher Job from the chart's test hook and sends one `WebsiteCreated` Kafka event. Event publishing failure triggers Helm rollback.

## Dynamic domain mapping

Each tenant chart release writes an Ingress rule for the configured host and asks cert-manager for a corresponding certificate. Adding another hostname means deploying another release with that host. The ingress controller and cluster do not need to be restarted. Local hosts use `*.localtest.me` DNS-to-loopback and the self-signed issuer; public hosting requires DNS pointed at a reachable ingress address and an ACME issuer.

## Observability

The kube-prometheus-stack chart supplies Prometheus, Grafana, Kubernetes exporters, and base dashboards. A ServiceMonitor in each tenant namespace scrapes `/metrics`. The included Grafana dashboard compares request rates and HPA replica counts and provides a Loki log panel. Fluentd reads node container logs, adds Kubernetes metadata, and sends them to the local Loki service.
