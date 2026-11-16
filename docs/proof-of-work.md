# Proof of work

One section per assignment item: **what to show**, **how to produce it**, and **what you should see**.
Text evidence is captured in one go; screenshots go in the same folder.

```sh
./scripts/collect-proof.sh          # writes docs/proof/<UTC time>/1-…5-*.txt (takes ~4 minutes with the load test)
```

| # | Item | Text evidence | Screenshots to add |
|---|---|---|---|
| 1 | Multi-tenant websites | `1-multi-tenant.txt` | browser on `https://user1/2/3.myyotta.com:8443/api/v1` |
| 2 | Scaling under load | `2-scaling.txt`, `load-test-output.txt` | Grafana "Website replicas" panel during the load |
| 3 | CI/CD with rollback and events | `3-cicd-rollback.txt` | Actions runs: CI green, CD approval, rollback run |
| 4 | Kafka integration | `4-kafka.txt` | optional: orchestrator log |
| 5 | Observability | `5-observability.txt` | Grafana dashboard, Loki log search |

Before you start, deploy the platform and the three tenants (`./scripts/e2e-setup.sh`, or the CD workflows),
and add the hosts line to `/etc/hosts`:

```text
127.0.0.1 provisioning.myyotta.com user1.myyotta.com user2.myyotta.com user3.myyotta.com
```

---

## 1. Multi-tenant websites deployed

**Show:** each tenant runs in its own namespace with its own app, PostgreSQL, Secret, Ingress host, TLS
certificate, HPA, PDB, quota and network policy, and one tenant cannot reach another's database.

```sh
helm list -A
kubectl get deploy,statefulset,ingress,hpa,pdb,resourcequota,networkpolicy -n user1
curl -k https://user1.myyotta.com:8443/api/v1         # {"service":"website-app","version":"v1"}
curl -k https://user2.myyotta.com:8443/health/ready   # {"status":"ready"}  (checked its own DB)
```

**Expect:** releases `user1`, `user2`, `user3` (plus `platform`, `website-provisioning`, `prometheus`),
every tenant `Ready`, certificates `READY=True`, and the isolation check in `1-multi-tenant.txt`
ending with `BLOCKED as expected`.

**New tenant without touching the cluster** (the live part of the demo):

```sh
curl -k -X POST https://provisioning.myyotta.com:8443/api/v1/websites \
  -H 'Content-Type: application/json' -d '{"tenant":"user4","domain":"user4.myyotta.com"}'
# 202 Accepted -> Kafka -> orchestrator -> "Website App Deployment" run (approve it) -> user4 namespace
```

📸 browser tabs for the three tenants; the `user4` CD run.

## 2. Scaling under load

**Show:** the HPA adds pods when CPU rises and removes them afterwards.

```sh
kubectl get hpa -n user1 -w                                     # terminal 1
python3 scripts/load_test.py --tenant user1 --seconds 180 --workers 40   # terminal 2
```

**Expect:** `REPLICAS` goes from 2 towards 8 (the chart's `maxReplicas`) while `cpu: x%/60%` is above
target; `kubectl describe hpa -n user1` lists `SuccessfulRescale` events. In `2-scaling.txt` the
15-second samples show the climb.

📸 Grafana → *Yotta Website Platform* → "Website replicas: current and desired" and "Website CPU usage by tenant".

## 3. CI/CD with rollback and events

**Show:** CI validates and publishes a versioned image, CD waits for approval and deploys with Helm,
a failed deploy rolls back automatically and sends a `WebsiteDeploymentFailed` event.

1. **CI:** any push → Actions shows Website App CI green; its summary shows the image tag
   (`1.0.0-YYYYMMDDHHMMSS`).
2. **CD with approval:** run *Website App Deployment* (`tenant=user1`, `domain=user1.myyotta.com`) → **Review deployments** → approve.
3. **Rollback:** run it again with **simulate_failure = true**:
   ```sh
   gh workflow run website_app_cd.yml --ref master -f tenant=user1 -f domain=user1.myyotta.com -f simulate_failure=true
   ```
   The release is deployed with a readiness probe that can never pass. After 2 minutes Helm rolls back,
   the job publishes `WebsiteDeploymentFailed` and fails (red, as it should). user1 keeps serving throughout.

**Expect:**
```sh
helm history user1 -n user1
# REVISION  STATUS      DESCRIPTION
# 5         superseded  Upgrade complete
# 6         failed      Upgrade "user1" failed: context deadline exceeded
# 7         superseded  Rollback to 5      <- Helm's own --rollback-on-failure
# 8         deployed    Rollback to 5      <- the deploy script's safety-net rollback (same good revision)
curl -k https://user1.myyotta.com:8443/health/ready      # still {"status":"ready"}
```
The run's artifact `website-deployment-evidence-<run id>` contains `user1-helm-history.txt` and `user1-events.txt`.

📸 the approval screen, the green deploy run, the red rollback run with its summary, `helm history`.

## 4. Kafka integration

**Show:** requests and deployment outcomes travel through Kafka, and every event was consumed.

```sh
kubectl exec -n platform kafka-0 -- /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:19092 --topic website-events --from-beginning --timeout-ms 10000
kubectl exec -n platform kafka-0 -- /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server localhost:19092 --describe --all-groups
kubectl logs -n platform deploy/website-orchestrator --tail=50
```

**Expect:** JSON events `WebsiteProvisioningRequested`, `WebsiteCreated` (and `WebsiteDeploymentFailed`
from the rollback demo) linked by the same `correlation_id`; consumer group `website-orchestrator`
with `LAG 0`; orchestrator log lines for "dispatched workflow" and the readable notifications.

## 5. Observability dashboards

**Show:** Prometheus scrapes cluster and website metrics, Grafana shows them, Fluentd ships logs to Loki.

```sh
kubectl port-forward -n observability svc/prometheus-grafana 3000:80
# http://localhost:3000  (admin / the password in helm/monitoring/values.yaml)
```

**Expect:** dashboard *Yotta Website Platform* with request rate by tenant/endpoint/status, success %,
replicas (HPA), CPU, memory, restarts and database readiness per tenant; the standard
*Kubernetes / Compute Resources* dashboards; **Explore → Loki** with `{namespace="user1"}` returns app logs.

📸 the dashboard while the load test runs; a Loki query.

---

### Extra: AIOps pod doctor

`gh workflow run aiops_pod_doctor_cd.yml --ref master -f image_tag=<tag> -f demo_failure=crash` breaks a demo
pod and puts Grok's root cause and workaround into the run summary. See [aiops-pod-doctor/README.md](../aiops-pod-doctor/README.md).

Earlier validation reports are kept in [docs/history/](history/).
