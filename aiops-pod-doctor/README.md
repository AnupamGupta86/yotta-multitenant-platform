# AIOps pod doctor

A small service that runs inside the kind cluster and explains failing pods.

Every **5 seconds** it:

1. lists the pods (all namespaces, or `WATCH_NAMESPACES`);
2. picks **one** failing pod it has not analysed yet;
3. collects the pod's status, events and logs (also from the previous crashed run);
4. masks anything that looks like a password or token, then asks **Grok** (xAI API) for
   *Root cause*, *Evidence*, *Workaround now* and *Permanent fix*;
5. prints the report in its log and keeps the last 20 reports at `GET /reports`.

Each failure is analysed once (per pod, container and reason), so a crash-looping pod does not
call Grok every 5 seconds. A pod that recovers and fails again is analysed again.
If Grok can't be reached (no key, network, API error) the report still comes, from built-in rules.

## What counts as failing

| Seen as | Example cause |
|---|---|
| `CrashLoopBackOff` (with the last exit reason, e.g. `OOMKilled`) | app exits on start |
| `ImagePullBackOff`, `ErrImagePull`, `InvalidImageName` | wrong image or tag |
| `CreateContainerConfigError`, `CreateContainerError`, `RunContainerError` | missing Secret / ConfigMap |
| `Unschedulable` (Pending) | not enough CPU/memory, taints |
| pod phase `Failed` (e.g. `Evicted`, a failed Job container) | node pressure, job error |

Pods that are starting, finished (`Succeeded`) or being deleted are ignored.

## Folder layout

| Path | |
|---|---|
| `service/` | Python code, unit tests, Dockerfile |
| `helm/` | Helm chart: Deployment, Service, read-only ServiceAccount/ClusterRole |
| `scripts/demo-failure.sh` | creates a broken demo pod (negative test) or cleans it up |
| `scripts/wait-for-report.sh` | used by CD: waits for the doctor's report on the demo pod |

The workflows live in `.github/workflows/aiops_pod_doctor_ci.yml` and `aiops_pod_doctor_cd.yml`
(GitHub only reads workflows from there).

## Code (in `service/`)

| File | Does |
|---|---|
| `src/main.py` | the 5-second loop, one report per poll, health and `/reports` HTTP endpoints |
| `src/detector.py` | decides if a pod is failing and why (pure function, easy to test) |
| `src/kube.py` | read-only Kubernetes API calls with the pod's service-account token |
| `src/llm.py` | builds the prompt, masks secrets, calls Grok, built-in fallback advice |
| `src/config.py` | settings from environment variables |

No third-party packages: only the Python standard library.

## Settings (environment variables, set by the Helm chart)

| Variable | Default | |
|---|---|---|
| `POLL_INTERVAL_SECONDS` | `5` | how often to look |
| `WATCH_NAMESPACES` | *(all)* | comma separated |
| `IGNORE_NAMESPACES` | *(none)* | comma separated |
| `LOG_TAIL_LINES` | `100` | log lines per container run |
| `GROK_API_KEY` | *(empty)* | from the `aiops-grok-api-key` Kubernetes Secret |
| `GROK_MODEL` | `grok-4` | |
| `GROK_API_URL` | `https://api.x.ai/v1/chat/completions` | |

## Pipelines

* **CI** `aiops_pod_doctor_ci.yml`: chart lint/validation/security scan, then the shared
  `service_ci.yml` (ruff, unit tests, bandit, pip-audit, secret scan, hadolint, image build,
  Trivy, container health check, publish `ghcr.io/<owner>/aiops-pod-doctor:1.0.0-<timestamp>`).
* **CD** `aiops_pod_doctor_cd.yml` (manual, needs approval on the `aiops-deployment` environment):
  stores `GROK_API_KEY` from that environment as a Kubernetes Secret, deploys with Helm into the
  `aiops` namespace, then (negative test) breaks a demo pod and waits for the doctor's report,
  which it adds to the run summary.

## Try it

```bash
# Run the deployment (after CI published a tag)
gh workflow run aiops_pod_doctor_cd.yml --ref master -f image_tag=<tag> -f demo_failure=crash

# Or break a pod yourself and watch
aiops-pod-doctor/scripts/demo-failure.sh crash            # or image-pull / missing-secret
kubectl logs -n aiops deploy/aiops-pod-doctor -f
kubectl get --raw /api/v1/namespaces/aiops/services/aiops-pod-doctor:http/proxy/reports
aiops-pod-doctor/scripts/demo-failure.sh cleanup

# Unit tests
cd aiops-pod-doctor/service && python -m pytest -q
```
