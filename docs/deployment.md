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

From GitHub Actions, run **Website Provisioning Day 0** on `master`. The workflow validates the dedicated chart, validates and publishes the Python image, then installs or upgrades the `website-provisioning` Helm release into the `platform` namespace on the Mac runner. Changes to the service or its chart trigger the same deployment automatically when pushed to `master`.

The provisioning release owns a separate PostgreSQL StatefulSet, persistent volume, and random password Secret. If your Kind cluster has no default storage class, set one in the chart values before installing it.

## 3. Submit a request

Port-forward the Provisioning API:

```sh
kubectl port-forward -n platform svc/website-provisioning 3001:80
```

Submit a tenant and domain to the FastAPI endpoint:

```sh
curl -i -X POST http://127.0.0.1:3001/api/v1/websites \
  -H 'Content-Type: application/json' \
  -d '{"tenant":"user4","domain":"user4.localtest.me"}'
```

The API returns `202 Accepted` and an event ID. It stores the request before publishing `WebsiteProvisioningRequested`. The orchestrator consumes that event and starts the deployment workflow.

The orchestrator dispatches the `deployment.yml` GitHub Actions workflow. That workflow validates the chart and website image, then runs Helm on the Mac. It adds the requested domain as an Ingress host without recreating the cluster.

## 4. Check the result

```sh
kubectl logs -n platform deployment/event-consumer --tail=100
kubectl logs -n platform deployment/website-provisioning --tail=100
kubectl get all,hpa,pdb,resourcequota,ingress -n user4
curl -k --resolve user4.localtest.me:8443:127.0.0.1 \
  https://user4.localtest.me:8443/health/ready
```

Successful deployments publish `WebsiteCreated`. If Helm rollout, the HTTPS health check, or the Helm test fails, the deployment script rolls back and GitHub Actions publishes `WebsiteDeploymentFailed`. The event consumer writes a readable notification to its logs and optionally posts to `NOTIFICATION_WEBHOOK_URL`. The deployment workflow uploads Kubernetes resource and event logs as a 14-day artifact.

For direct deployment of the three sample tenants, use:

```sh
IMAGE=ghcr.io/<owner>/website-api:<tag> \\
EVENT_IMAGE=ghcr.io/<owner>/website-event-consumer:<tag> \\
PULL_IMAGE=true ./scripts/deploy-platform.sh
```

The workflow's manual dispatch can deploy one DNS-safe tenant name or all three sample tenants. A custom domain applies to one tenant at a time.

## What CI checks before it deploys

The service pipeline runs Python lint and unit tests, Bandit checks for risky code patterns, `pip-audit` checks dependencies, and Gitleaks checks the repository for committed credentials. It lints each Dockerfile, builds and scans the image, then starts the API with a temporary PostgreSQL database and checks its health endpoint. The event-consumer pipeline also publishes a test event to a real Kafka broker.

Each pushed image is tagged `major.minor.patch-UTCtimestamp-run<GitHub run ID>`, for example `1.0.0-20261115040530-run123456789`. Update the repository's `VERSION` file to change the major, minor, or patch part.

The deployment pipelines lint and render the Helm charts, validate their Kubernetes resources with Kubeconform, and scan the rendered resources for high-risk security settings. Trivy's image scan reports a false positive for setuptools 70.3.0 from a replaced image layer; the final image contains fixed setuptools 80.10.2. That one advisory is documented in `.trivyignore`; other high and critical image findings still fail CI.

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
