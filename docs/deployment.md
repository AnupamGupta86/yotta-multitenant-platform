# Deployment guide

## 1. Prepare the Mac runner and cluster

Install Docker Desktop, Kind, kubectl, Helm 3.17+, Python 3.12+, and a self-hosted GitHub Actions runner. Add the runner label `yotta-local-k8s`.

Create a fine-grained token for this repository with **Actions: Read and write**. That permission allows the in-cluster orchestrator to call GitHub's workflow-dispatch endpoint. Keep the token in your shell environment and do not commit it:

```sh
export ORCHESTRATOR_REPOSITORY=owner/repository
export ORCHESTRATOR_GITHUB_TOKEN=your-fine-grained-token
./scripts/setup-kind.sh
```

The setup script installs cluster add-ons through Helm and installs the platform release. The platform includes Kafka and the deployment orchestrator, but it intentionally does not install the Provisioning API.

## 2. Install the Day 0 Provisioning API

From GitHub Actions, run **Website Provisioning Deployment** on `master` and provide the published `image_tag`. The workflow validates the chart, then pulls the selected image and installs or upgrades the `website-provisioning` Helm release in the `platform` namespace on the self-hosted Mac runner. Changes to the service run CI and publish an image; deployment remains manual.

The provisioning release owns a separate PostgreSQL StatefulSet, persistent volume, and random password Secret. If your Kind cluster has no default storage class, set one in the chart values before installing it.

## 3. Submit a request

The platform Ingress serves the Provisioning API at `provisioning.myyotta.com`. Each tenant has its own Ingress host, created by the tenant Helm release. For local Kind testing, add these entries to `/etc/hosts` on the Mac:

```text
127.0.0.1 provisioning.myyotta.com user1.myyotta.com user2.myyotta.com user3.myyotta.com
```

Kind maps HTTPS to port `8443`, so use that port in local requests. The local certificate is self-signed; `curl -k` skips certificate verification during development.

Submit a tenant and domain to the FastAPI endpoint:

```sh
curl -k -i -X POST https://provisioning.myyotta.com:8443/api/v1/websites \
  -H 'Content-Type: application/json' \
  -d '{"tenant":"user4","domain":"user4.myyotta.com"}'
```

The API returns `202 Accepted` and an event ID. It stores the request before publishing `WebsiteProvisioningRequested`. The orchestrator consumes that event and dispatches **Website App Deployment**. The workflow installs the tenant chart with the requested domain, creating its host rule automatically; no DNS or Ingress edit is needed for each tenant while testing locally.

Website App CI validates and publishes the website image. The request-triggered `website_app_cd.yml` workflow receives the tenant, domain, image override, and correlation ID, then runs Helm on the self-hosted Mac runner. It uses the latest CI-published website image when no image override is supplied and adds the requested domain as an Ingress host without recreating the cluster.

## 4. Check the result

```sh
kubectl logs -n platform deployment/event-consumer --tail=100
kubectl logs -n platform deployment/website-provisioning --tail=100
kubectl get all,hpa,pdb,resourcequota,ingress -n user4
curl -k https://user4.myyotta.com:8443/health/ready
```

Successful deployments publish `WebsiteCreated`. If Helm rollout, the HTTPS health check, or the Helm test fails, the deployment script rolls back and GitHub Actions publishes `WebsiteDeploymentFailed`. The event consumer writes a readable notification to its logs and optionally posts to `NOTIFICATION_WEBHOOK_URL`. The deployment workflow uploads Kubernetes resource and event logs as a 14-day artifact.

For direct deployment of the three sample tenants, use:

```sh
IMAGE=ghcr.io/<owner>/website-app:<tag> \\
EVENT_IMAGE=ghcr.io/<owner>/website-event-consumer:<tag> \\
PULL_IMAGE=true ./scripts/deploy-platform.sh
```

The workflow's manual dispatch can deploy one DNS-safe tenant name or all three sample tenants. A custom domain applies to one tenant at a time.

## What CI checks before it deploys

The service pipeline runs Python lint and unit tests, Bandit checks for risky code patterns, `pip-audit` checks dependencies, and Gitleaks checks the repository for committed credentials. It lints each Dockerfile, builds and scans the image, then starts the API with a temporary PostgreSQL database and checks its health endpoint. The event-consumer pipeline also publishes a test event to a real Kafka broker.

Each pushed image is tagged `major.minor.patch-UTCtimestamp-run<GitHub run ID>`, for example `1.0.0-20261115040530-run123456789`. Update the repository's `VERSION` file to change the major, minor, or patch part.

The platform workflow renders and schema-validates the pinned cluster add-on charts, the platform chart, and the pinned Prometheus/Grafana chart. It scans project-managed platform resources, scans Fluentd's image, then deploys cluster add-ons, the platform release, and monitoring through Helm on the Mac runner. Website App Deployment and Website Provisioning Deployment also target the self-hosted Mac runner and use Helm for Kubernetes changes. The monitoring chart's remaining inherited Trivy findings are listed with reasons in [helm/monitoring/trivyignore](/Users/ashagupta/yotta-multitenant-platform/helm/monitoring/trivyignore). The CI workflows lint and render tenant and provisioning charts, validate their Kubernetes resources with Kubeconform, and scan rendered resources for high-risk security settings. Trivy's image scan reports a false positive for setuptools 70.3.0 from a replaced image layer; the final image contains fixed setuptools 80.10.2. That one advisory is documented in `.trivyignore`; other high and critical image findings still fail CI.

## Scaling demo

Watch a tenant's HPA, then generate CPU work:

```sh
kubectl get hpa -n user1 -w
python3 scripts/load_test.py --tenant user1 --seconds 180 --workers 40
```

Each tenant has CPU and memory requests/limits, CPU and memory HPA targets, a ResourceQuota, a LimitRange, and a PDB. Prometheus scrapes the website metrics and Grafana provides the dashboard. Fluentd forwards pod logs to Loki.

## Local development

Docker Compose runs local PostgreSQL, Kafka, the FastAPI services, and the event consumer. Forward or call the provisioning API on `http://127.0.0.1:3001`. The consumer defaults to dry-run mode so local requests do not start real GitHub workflows. To exercise dispatch from Compose, set `ORCHESTRATOR_DRY_RUN=false`, `ORCHESTRATOR_REPOSITORY`, and `ORCHESTRATOR_GITHUB_TOKEN`.

For actual Kubernetes deployment, use Helm releases. The platform, Day 0 API, and every tenant deployment have separate charts/releases. The setup and deployment scripts use `kubectl` only for readiness checks, port forwarding, logs, and inspection.
