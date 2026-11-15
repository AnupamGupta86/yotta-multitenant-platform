# Assignment validation record

This document maps the assignment scenarios to the implementation and records the checks run against the local Kind cluster. The assignment asks for isolated tenant deployments and dynamic domains, CI/CD with rollback and Kafka events, scaling and observability, and a single-node Kafka event flow. The results below distinguish checks completed on the cluster from checks that need a registered Mac runner or a network-enabled schema fetch.

## Result summary

| Scenario | Result | Evidence |
| --- | --- | --- |
| Multi-tenant deployments, host routing, TLS, health probes, resources, network policies | Pass for `user1`, `user2`, and `user3` | Separate namespaces, HTTPS host routes and Ready certificates; health endpoints succeeded; cross-tenant database connection was blocked |
| CI build, image validation, Helm deployment, rollback, multi-branch/user | Partial | Static CI checks and local Helm rollback passed. GitHub deployment workflow was queued because the repository had no registered self-hosted Mac runner. |
| HPA, quota, PDB, load demonstration, metrics and dashboard | Pass | HPA scaled user1 from 2 to 8 replicas during a 90-second load; 3,594 requests succeeded, 0 failed; Prometheus targets/metrics and Grafana health/dashboard checked |
| Kafka events and consumer notification | Pass for sample tenant deployments and consumer | `WebsiteCreated` events for user1–user3 were consumed; consumer group lag was 0. A separate `user4` provisioning event was dispatched to GitHub but its workflow is waiting for the Mac runner. |

## 1. Multi-tenant routing and isolation

Each website release creates its own namespace, website Deployment/Service, PostgreSQL StatefulSet and persistent volume, ingress host, TLS certificate, NetworkPolicies, ResourceQuota, LimitRange, HPA and PDB. The Provisioning API runs in `platform` and owns a separate PostgreSQL StatefulSet. Tenant database credentials are generated as Kubernetes Secrets by Helm; passwords are not chart values.

### Check tenant resources and separate database storage

```sh
for tenant in user1 user2 user3; do
  kubectl get deployment,service,statefulset,pvc,ingress,hpa,pdb,resourcequota,networkpolicy -n "$tenant"
done
```

**Observed:** the three tenant namespaces each had website workloads and a PostgreSQL StatefulSet/PVC. All tenant PVCs were `Bound`. The platform namespace separately contained the provisioning API/database and platform Kafka/website-orchestrator components.

### Check automatic host routing and TLS

For local Kind ingress, the Mac must resolve the local test hostnames to loopback. Add or confirm:

```text
127.0.0.1 provisioning.myyotta.com user1.myyotta.com user2.myyotta.com user3.myyotta.com
```

Kind exposes HTTPS ingress on port `8443`; certificates use a local self-signed issuer, so `curl -k` is appropriate for this local check:

```sh
for host in user1 user2 user3; do
  curl -kfsS "https://${host}.myyotta.com:8443/api/v1"
  curl -kfsS "https://${host}.myyotta.com:8443/health/ready"
done
kubectl get certificate -A
```

**Observed:** each host routed to its corresponding tenant website and returned `{"service":"website-app","version":"v1"}`. Every readiness endpoint reported ready after checking its own database. Certificates for provisioning and all three tenant hosts were `Ready=True`. The ingress rules use host routing, so adding a tenant chart release adds its host rule without recreating the cluster. Public DNS and publicly trusted TLS still require a real domain, DNS records, and an appropriate issuer.

### Check probes, resource policies, and tenant network isolation

```sh
kubectl describe deployment user1-app -n user1
kubectl get limitrange,resourcequota,networkpolicy,pdb,hpa -n user1
```

**Observed:** website containers had readiness/liveness probes and CPU/memory requests and limits. Quota and LimitRange were present; PDB `minAvailable` was 1. From a user1 pod, a connection attempt to user2's PostgreSQL service timed out (`connect_ex=11`), showing tenant NetworkPolicies denied cross-tenant database access.

## 2. CI/CD, Helm deployment, rollback, and events

All Kubernetes installation and application deployment workflows use Helm releases. The platform release owns shared infrastructure; the provisioning API has its own manual Day 0 workflow; the website app has a request-triggered deployment workflow. The website CI builds and pushes images, while the local Mac runner performs tenant deployments. The branch filters accept `master`, `feature`, and `feature/*` for the relevant deployment workflow.

### Validate workflow files and deployment tooling

```sh
actionlint .github/workflows/*.yml
shellcheck scripts/*.sh
helm lint helm/platform helm/website helm/website-provisioning
git diff --check
```

**Observed:** actionlint and shellcheck returned successfully; Helm reported all three application/platform charts linted with 0 failures; `git diff --check` returned clean. The optional Calico CRD chart has no values/templates and emits Helm informational messages, not a lint failure.

Service CI includes Python lint/unit tests, Bandit, dependency auditing, secret scanning, Dockerfile lint, container build and Trivy image scan, plus service health/functional checks. The local test suites run in the service images: website-app **8 passed**, website-orchestrator **11 passed**, and website-provisioning **7 passed** using the compatible `unittest` runner. A follow-up attempt to run pytest in a temporary provisioning image failed because that image's installed pytest imports Python's removed `imp` module; this is why the recorded passing service suite uses unittest. CI was updated to install `httpx2`, which the installed Starlette TestClient requires.

Trivy image scans of the three service images and Trivy config scans of rendered platform/provisioning/tenant manifests completed with no high or critical findings. Hadolint completed without findings. Kubeconform could not download its schemas from `raw.githubusercontent.com` in this environment, so schema validation is **not independently confirmed here**; the chart CI step remains configured to run it where network access is available.

### Verify the request-to-deployment workflow

The local user flow is:

```sh
curl -k -i -X POST https://provisioning.myyotta.com:8443/api/v1/websites \
  -H 'Content-Type: application/json' \
  -d '{"tenant":"user4","domain":"user4.myyotta.com"}'
```

The expected response is `202 Accepted`; the website orchestrator consumes `WebsiteProvisioningRequested` and dispatches `Website App Deployment`. On successful tenant deployment, the workflow publishes `WebsiteCreated`. On Helm/health-check failure, it rolls back and publishes `WebsiteDeploymentFailed`. The website orchestrator writes a notification to its logs and can also send it to `NOTIFICATION_WEBHOOK_URL`.

**Observed:** an invalid request returned HTTP `422` and emitted no provisioning event. The Kafka topic contained `WebsiteProvisioningRequested` for `user4`, and GitHub accepted dispatch of workflow run `43675206685`. Its state was pending with no jobs because the repository had **zero registered self-hosted runners**. Therefore the `user4` deployment did not happen. The Kafka consumer group had offset 4, log-end offset 4, and lag 0 for the events observed. The three sample tenant `WebsiteCreated` events were consumed.

To activate automatic local deployment, register and run a Mac GitHub Actions runner with labels `self-hosted`, `macOS`, and `yotta-local-k8s`, using the instructions in [local-runner.md](local-runner.md). The runner must use the same Mac user and Docker context as the Kind cluster. After workflow changes are pushed, re-run GitHub CI and confirm the new run is green; current local changes have not been pushed to GitHub.

### Exercise deployment rollback

Rollback was tested against user3 with a deliberately nonexistent image tag. This is an intentional failure injection; do not use this tag for a normal release.

```sh
helm upgrade --install user3 helm/website -n user3 \
  --set tenant.name=user3 \
  --set image.tag=assignment-rollback-test-does-not-exist \
  --server-side=false --rollback-on-failure --wait --timeout 45s
```

**Expected/observed:** Helm reported the failed image rollout and rolled the release back. The prior user3 app and PostgreSQL pods returned to `Running`; both `/api/v1` and `/health/ready` passed afterward. The live GitHub workflow dispatch could not be exercised end-to-end until a Mac runner is registered.

## 3. Scaling, quotas, availability, and monitoring

Tenant charts configure an HPA with CPU and memory targets, per-tenant quota/LimitRange and a PDB. The CPU load demo exercises CPU-based scaling; memory scaling is configured but was not separately stress-tested.

### Demonstrate autoscaling

Watch the HPA in one terminal, then run the load generator in another:

```sh
kubectl get hpa -n user1 -w
python3 scripts/load_test.py --tenant user1 --domain user1.myyotta.com --port 8443 --seconds 90 --workers 30
```

If local DNS is not configured, add the host mapping shown above or use a host-resolving test client. **Observed:** 3,594 requests completed successfully and 0 failed. HPA increased the website from 2 to 6 and then 8 replicas; all 8 became available. The CPU metric rose above its 60% target. After load ended, the HPA scaled back down. A few readiness probe warnings occurred during saturation/termination, but the load client had no failed requests and the full 8-replica set became ready. The tenant quota was increased to 10 CPU limit units so that eight app replicas plus the PostgreSQL pod can fit; the earlier quota of 8 prevented the final HPA replica from scheduling.

### Check Prometheus, Grafana, and logs

```sh
kubectl get servicemonitor -A
kubectl port-forward -n observability svc/prometheus-kube-prometheus-prometheus 9090:9090
kubectl port-forward -n observability svc/prometheus-grafana 3000:80
```

In another terminal, query Prometheus for `up` and `yotta_http_requests_total`. Grafana is available at `http://localhost:3000`; its provisioned website dashboard includes per-tenant request rate, desired/current replicas, and Loki website logs. Fluentd ships container logs to Loki.

**Observed:** all tenant Prometheus targets were up, `yotta_http_requests_total` was present for each tenant, the Grafana health API returned HTTP 200 with database status `ok`, and the `website-platform-dashboard` ConfigMap existed. Fluentd and application logs had no recent error/exception matches after startup. Prometheus/Grafana port-forward sessions responded successfully during the audit. If those sessions have ended, rerun the commands above in separate terminals.

## 4. Kafka deployment events

Kafka runs as a single broker in the Kind platform release. The Compose environment also includes a Kafka broker for local development. Website deployment publishes `WebsiteCreated` after successful Helm deployment; the workflow publishes `WebsiteDeploymentFailed` after rollback/failure. The website orchestrator logs notification details and optionally posts to a webhook.

### Verify broker topic and consumer

```sh
kubectl logs -n platform deployment/website-orchestrator --since=15m
kubectl exec -n platform deploy/kafka -- \
  kafka-consumer-groups --bootstrap-server localhost:9092 \
  --describe --group deployment-orchestrator
```

**Observed:** `website-events` contained `WebsiteCreated` for user1, user2, and user3, plus the `WebsiteProvisioningRequested` event for user4. The orchestrator consumer group showed no lag for the observed records. A harmless `WebsiteCreated` sample was also published to the Compose Kafka broker, and its consumer logged `Consumed event`.

## Remaining actions and limitations

1. Register and start the self-hosted Mac runner with the required labels; this is required for GitHub Actions to deploy into the Mac's Kind cluster. As checked during the audit, GitHub reported zero runners and the user4 deployment run remained pending.
2. Push the current changes and verify fresh GitHub runs, including the provisioning service CI and deployment workflow. Existing GitHub results predate the local validation fixes.
3. Once the runner is online, retry the `user4` workflow and confirm its host, TLS certificate, readiness, and `WebsiteCreated` event.
4. Kubeconform could not download schemas due to network/DNS restrictions during this audit. Confirm that step in a network-enabled CI run.
5. The local TLS certificates are self-signed. Production/public access needs real DNS and a trusted certificate issuer.
6. An older Docker Compose stack (`database-api`, `provisioning-api`, `postgres`, Kafka, and website-orchestrator) was already running separately on ports 3001/3002/5432/9092. It was left untouched to protect its data; stop/clean it deliberately when no longer needed, and avoid confusing its endpoints with the fresh Kind environment.

The user-requested implementation preference for Python is met by the Python services. The website app currently uses Python's standard-library HTTP server; FastAPI is used for the provisioning API.
