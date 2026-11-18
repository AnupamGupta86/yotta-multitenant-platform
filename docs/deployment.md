# Deployment guide

## One-command local setup

For a complete local Kind setup, run this from the repository root:

```sh
./scripts/setup-local-e2e.sh
```

The utility creates a fresh `yotta-platform` Kind cluster, installs cluster add-ons, monitoring, and the platform, then builds and deploys the local Provisioning API and all three assignment tenants: `user1`, `user2`, and `user3` at their matching `*.myyotta.com` domains. If that cluster already exists, the script warns that its workloads and demo data will be deleted and requires you to type `delete yotta-platform`; a non-interactive run refuses to delete it. The script derives `ORCHESTRATOR_REPOSITORY` from the `origin` Git remote and takes `ORCHESTRATOR_GITHUB_TOKEN` from the environment, otherwise from your GitHub CLI login (`gh auth token`), otherwise it asks for it. Use a fine-grained token with Actions read/write access. To install a single tenant instead, pass its name and optional domain, for example `./scripts/setup-local-e2e.sh user4 user4.example.com`.

The utility does not edit `/etc/hosts`; add the line it prints after setup if the hostnames do not resolve locally. It also does not register a GitHub Actions runner. The existing manual setup and GitHub Actions deployment steps follow below for teams using published images or remote deployments.

## 1. Prepare the Mac runner and cluster

Install a Docker engine (Colima or Docker Desktop), Kind, kubectl, Helm 4.x, Python 3.12+, and a self-hosted GitHub Actions runner. Add the runner label `yotta-local-k8s`.

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
127.0.0.1 provisioning.myyotta.com user1.myyotta.com user2.myyotta.com user3.myyotta.com grafana.myyotta.com prometheus.myyotta.com alertmanager.myyotta.com
```

Kind maps HTTPS to port `8443`, so use that port in local requests. The local certificate is self-signed; `curl -k` skips certificate verification during development.

Submit a tenant and domain to the FastAPI endpoint:

```sh
curl -k -i -X POST https://provisioning.myyotta.com:8443/api/v1/websites \
  -H 'Content-Type: application/json' \
  -d '{"tenant":"user4","domain":"user4.myyotta.com"}'
```

The API returns `202 Accepted` and an event ID. It stores the request before publishing `WebsiteProvisioningRequested`. The orchestrator consumes that event and dispatches **Website App Deployment**. The workflow installs the tenant chart with the requested domain, creating its host rule automatically; no DNS or Ingress edit is needed for each tenant while testing locally.

Website App CI validates and publishes the website image. The request-triggered `website_app_cd.yml` workflow receives the tenant, domain, an optional `image_tag` and the correlation ID, then runs Helm on the self-hosted Mac runner. It uses the newest CI-published website image when no `image_tag` is supplied, and the website-orchestrator image that is currently running in the cluster unless `orchestrator_image_tag` is given. It adds the requested domain as an Ingress host without recreating the cluster.

## 4. Check the result

```sh
kubectl logs -n platform deployment/website-orchestrator --tail=100
kubectl logs -n platform deployment/website-provisioning --tail=100
kubectl get all,hpa,pdb,resourcequota,ingress -n user4
curl -k https://user4.myyotta.com:8443/health/ready
```

Successful deployments publish `WebsiteCreated`. If Helm rollout, the HTTPS health check, or the Helm test fails, the deployment script rolls back and GitHub Actions publishes `WebsiteDeploymentFailed`. The website orchestrator writes a readable notification to its logs and optionally posts to `NOTIFICATION_WEBHOOK_URL`. The deployment workflow uploads Kubernetes resource and event logs as a 14-day artifact.

For direct deployment of the three sample tenants, use:

```sh
IMAGE=ghcr.io/<owner>/website-app:<tag> \
ORCHESTRATOR_IMAGE=ghcr.io/<owner>/website-orchestrator:<tag> \
PULL_IMAGE=true ./scripts/deploy-sample-tenants.sh
```

The workflow's manual dispatch can deploy one DNS-safe tenant name or all three sample tenants. A custom domain applies to one tenant at a time.

## What CI checks before it deploys

The service pipeline runs Python lint and unit tests, Bandit checks for risky code patterns, `pip-audit` checks dependencies, and Gitleaks checks the repository for committed credentials. It lints each Dockerfile, builds and scans the image, then starts the API with a temporary PostgreSQL database and checks its health endpoint. The website-orchestrator pipeline also publishes a test event to a real Kafka broker.

Each pushed image is tagged `MAJOR.MINOR.PATCH-YYYYMMDDHHMMSS` (UTC build time), for example `1.0.0-20261116010802`. The deployment workflows reject any other format before touching the registry or the cluster. The repository owner portion of the GHCR image name is normalized to lowercase. Update the repository's `VERSION` file to change the major, minor, or patch part.

`platform_ci.yml` renders and schema-validates the pinned cluster add-on charts, platform chart, and Prometheus/Grafana chart; it scans project-managed resources and Fluentd's image, then publishes a versioned Fluentd image. After CI succeeds, run `platform_cd.yml` manually with that image tag to install cluster add-ons, the platform release, and monitoring through Helm on the Mac runner. Website App Deployment and Website Provisioning Deployment also target the self-hosted Mac runner and use Helm for Kubernetes changes. The monitoring chart's remaining inherited Trivy findings are listed with reasons in [helm/monitoring/.trivyignore](../helm/monitoring/.trivyignore). The CI workflows lint and render tenant and provisioning charts, validate their Kubernetes resources with Kubeconform, and scan rendered resources for high-risk security settings. Trivy's image scan reports a false positive for setuptools 70.3.0 from a replaced image layer; the final image contains fixed setuptools 80.10.2. That one advisory is documented in `.trivyignore`; other high and critical image findings still fail CI.

Before running **Platform Deployment** or **Website Orchestrator Deployment**, add the repository Actions secret `ORCHESTRATOR_GITHUB_TOKEN` (both store it in the cluster for the orchestrator, and both stop with an error if it is missing). Use a fine-grained token scoped to this repository with **Actions: Read and write** so the orchestrator can dispatch Website App Deployment. The workflow passes `github.repository` as `orchestrator.repository` and the secret as `orchestrator.githubToken` during the platform Helm upgrade.

### Manual approval

Every deployment pipeline waits for a person to approve it before it changes anything. Each one deploys through its own GitHub environment, so an approval covers exactly one kind of change and each has its own deployment history:

| Pipeline | Environment |
|---|---|
| Platform Deployment (`platform_cd.yml`) | `platform-deployment` |
| Website Orchestrator Deployment (`website_orchestrator_cd.yml`) | `website-orchestrator-deployment` |
| Website Provisioning Deployment (`website_provisioning_cd.yml`) | `website-provisioning-deployment` |
| Website App Deployment (`website_app_cd.yml`, one run per tenant) | `website-app-deployment` |
| AIOps Pod Doctor Deployment (`aiops_pod_doctor_cd.yml`) | `aiops-deployment` (also holds `GROK_API_KEY`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `REPORT_EMAIL_TO`) |

Set them up once with `scripts/setup-deployment-approvals.sh` (needs the GitHub CLI, logged in with admin rights). It creates the five environments, makes you the required reviewer, and only allows deployments from `master`, `feature` and `feature/*`, the same branches the CD jobs accept. To add more reviewers: `scripts/setup-deployment-approvals.sh alice bob`.

When a deployment starts, the run shows **Review deployments**: open it, tick the environment and choose **Approve and deploy** (or **Reject**). Nothing runs on the Mac until then.

Website App Deployment is started automatically by the orchestrator when a website is requested, so every new tenant website also waits for approval. Without required reviewers on an environment, its job runs without stopping, so the setup script is what makes the gate real.

## Scaling demo

Watch a tenant's HPA, then generate CPU work:

```sh
kubectl get hpa -n user1 -w
python3 scripts/load-test.py --tenant user1 --seconds 180 --workers 40
```

Each tenant has CPU and memory requests/limits, CPU and memory HPA targets (60 % / 75 %, 2–8 replicas), a ResourceQuota, a LimitRange, and a PDB. Prometheus scrapes the website metrics and Grafana provides the dashboard. Fluentd forwards pod logs to Loki.

To demonstrate a rollback, run Website App Deployment with `simulate_failure=true`; see [rollback.md](rollback.md).

## Local development

Docker Compose runs local PostgreSQL, Kafka, the FastAPI services, and the website orchestrator. Forward or call the provisioning API on `http://127.0.0.1:3001`. The orchestrator defaults to dry-run mode so local requests do not start real GitHub workflows. To exercise dispatch from Compose, set `ORCHESTRATOR_DRY_RUN=false`, `ORCHESTRATOR_REPOSITORY`, and `ORCHESTRATOR_GITHUB_TOKEN`.

For actual Kubernetes deployment, use Helm releases. The platform, Day 0 API, and every tenant deployment have separate charts/releases. The setup and deployment scripts use `kubectl` only for readiness checks, port forwarding, logs, and inspection.
