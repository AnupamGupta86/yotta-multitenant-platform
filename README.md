# Yotta multitenant website platform

Local-first DevOps assignment implementation. The APIs, event producer/consumer, and tests are Python. Docker and GitHub Actions validate the services, build and publish images, and deploy the platform and website APIs into a local Kind cluster on the Mac through a self-hosted runner.

## What is included

- Three isolated website namespaces: `user1`, `user2`, and `user3`; each chart release has its own PostgreSQL StatefulSet and secret.
- The provisioning API and database API run in the `platform` namespace with separate PostgreSQL StatefulSets and Helm-managed credential Secrets.
- Kafka, the Python event consumer, Loki, Fluentd, the local certificate issuer, and the Grafana dashboard are also resources in the Helm-managed `platform` release.
- Dynamic host-based Ingress (`user1.localtest.me`, etc.) with TLS certificates issued automatically by cert-manager. The local issuer is self-signed; use an ACME ClusterIssuer for public domains.
- Readiness/liveness probes, tenant resource quotas and LimitRanges, CPU/memory HPA, PDB, and namespace-scoped NetworkPolicies.
- Prometheus metrics and a Grafana dashboard; Fluentd collects Kubernetes container logs and writes them to Loki, which is available as a Grafana datasource.
- A single-node Kafka KRaft broker and Python consumer in Kubernetes; a Helm test hook publishes `WebsiteCreated` only after the deployment and HTTPS smoke check pass.
- Reusable service CI for provisioning API, database API, and website API, plus a Kafka consumer workflow. Checks include tests, Ruff, Bandit, pip-audit, Gitleaks, Hadolint, Trivy image scanning, and a Docker runtime health probe. Successful images are published to GHCR with an SBOM and provenance.
- A self-hosted macOS GitHub Actions deployment workflow, Helm rollback, and downloadable deployment evidence.

## Prerequisites on the Mac

Install and start Docker Desktop, then install `kind`, `kubectl`, Helm 3.17+, `gh`, and Python 3.12+. Ingress binds loopback ports 8081 and 8443 to avoid conflicts with existing Mac services.

```sh
./scripts/setup-kind.sh
```

The setup script creates the `yotta-platform` Kind cluster and uses Helm releases for Calico, ingress-nginx, metrics-server, cert-manager, kube-prometheus-stack, Loki, Fluentd, Kafka, the APIs, and their databases. The local `platform` chart also owns the ClusterIssuer and Grafana dashboard. The website chart owns its namespace resources and database credentials. Kind needs explicit host port mappings for Docker Desktop, and Calico replaces kindnet so tenant NetworkPolicies are actually enforced.

Docker Compose remains available as an optional local development and CI test fixture; it is not used by the deployment workflow. For local API development, it initializes separate `provisioning`, `database_api`, `website_user1`, `website_user2`, and `website_user3` databases. These demo credentials are intentionally not production secrets. Kubernetes creates separate API databases and one database per website tenant, with Helm-managed random password Secrets. Demo database data uses `emptyDir`, so deleting a tenant namespace or the local Kind cluster deletes its database data.

The current Kind cluster was originally bootstrapped with Calico raw manifests. The updated setup script refuses to silently mix that installation with the Helm-managed Calico release. Back up any demo data and recreate that cluster before using the new Helm-only setup.

## GitHub Actions deployment on this Mac

Register a self-hosted GitHub runner on the Mac with the custom label `yotta-local-k8s`. Follow [the local runner guide](docs/local-runner.md). The deployment workflow uses GitHub-hosted runners to validate and publish `website-api`, then runs only the deploy step on the Mac. The Mac runner must share the local user's Docker context and Kind cluster.

Push to `master` to deploy all three tenants after CI succeeds, or choose **Actions → Local Kind deployment → Run workflow** to deploy one tenant or all three from `master` or a `feature/*` branch. An optional domain input lets you map a custom host to the local ingress. The runner pulls the published images, loads them into Kind, performs Helm upgrades, checks HTTPS readiness, then runs the website chart's Helm test hook to publish a Kafka event. If deployment, the smoke check, or event publication fails, Helm rolls back the website release.

```sh
# Manual local deployment without GitHub Actions
IMAGE=ghcr.io/<owner>/website-api:<image-tag> ./scripts/deploy-platform.sh

# Capture namespace, HPA, ingress, and Kafka evidence
./scripts/verify-deployment.sh user1 user1.localtest.me

# Generate CPU load for the autoscaling demonstration (watch HPA in another terminal)
kubectl get hpa -n user1 -w
python3 scripts/load_test.py --tenant user1 --seconds 180 --workers 40
```

Domains are independent Ingress resources. Add a tenant by deploying a chart release in a new namespace and supplying its domain; no ingress-controller restart or cluster redeployment is needed. `localtest.me` resolves to loopback for local demonstrations. For real custom domains, point DNS to the ingress address and switch from `local-selfsigned` to a public ACME issuer.

## Access and evidence

- Websites: `https://user1.localtest.me:8443`, `https://user2.localtest.me:8443`, `https://user3.localtest.me:8443` (self-signed TLS; use `curl -k` for local checks).
- Grafana: run `kubectl port-forward -n observability svc/prometheus-grafana 3000:80`, then open `http://localhost:3000`; user `admin`, password `local-demo-only-change-me`.
- Prometheus: `kubectl port-forward -n observability svc/prometheus-kube-prometheus-prometheus 9090:9090`.
- Provisioning API: run `kubectl port-forward -n platform svc/provisioning-api 3001:80`, then open `http://localhost:3001/api/v1`; Database API: run `kubectl port-forward -n platform svc/database-api 3002:80`.
- Kafka is in-cluster at `kafka.platform.svc.cluster.local:19092`; consumer logs: `kubectl logs -n platform deployment/event-consumer -f`.
- Deployment artifacts include Kubernetes resources and Kafka consumer logs. `scripts/verify-deployment.sh` writes local evidence into the ignored `output/` directory.

The local Grafana password, self-signed certificates, single-node Kafka, and in-memory Loki are suitable for the assignment demo only. Do not expose these services to the public internet or reuse the demo secrets in production.
