#!/usr/bin/env bash
# Create (or remove) a deliberately broken pod, so the AIOps pod doctor has something to diagnose.
#
#   aiops-pod-doctor/scripts/demo-failure.sh crash        # app exits with a database error -> CrashLoopBackOff
#   aiops-pod-doctor/scripts/demo-failure.sh image-pull   # image tag does not exist       -> ImagePullBackOff
#   aiops-pod-doctor/scripts/demo-failure.sh missing-secret  # refers to a missing Secret  -> CreateContainerConfigError
#   aiops-pod-doctor/scripts/demo-failure.sh cleanup      # delete the demo namespace
#
# Prints the demo pod name. Namespace: $AIOPS_DEMO_NAMESPACE (default aiops-demo).
set -euo pipefail

mode="${1:-crash}"
namespace="${AIOPS_DEMO_NAMESPACE:-aiops-demo}"
pod="broken-$mode"

if [[ "$mode" == cleanup ]]; then
  kubectl delete namespace "$namespace" --ignore-not-found --wait=false
  exit 0
fi

case "$mode" in
  crash)
    image=busybox:1.37
    command='["sh", "-c", "echo Starting orders-api v2.3.1; echo Connecting to postgres at orders-db:5432; sleep 2; echo FATAL: could not connect to server: Connection refused; echo Is the server running on host orders-db and accepting TCP/IP connections on port 5432?; exit 1"]'
    env='[]'
    ;;
  image-pull)
    image=busybox:does-not-exist-0.0.0
    command='["sh", "-c", "sleep 3600"]'
    env='[]'
    ;;
  missing-secret)
    image=busybox:1.37
    command='["sh", "-c", "sleep 3600"]'
    env='[{"name": "DB_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "orders-db-credentials", "key": "password"}}}]'
    ;;
  *)
    echo "Unknown mode '$mode' (use crash, image-pull, missing-secret or cleanup)" >&2
    exit 2
    ;;
esac

# A namespace removed by an earlier cleanup may still be terminating
if [[ "$(kubectl get namespace "$namespace" -o jsonpath='{.status.phase}' 2>/dev/null)" == Terminating ]]; then
  kubectl wait --for=delete namespace/"$namespace" --timeout=120s >/dev/null
fi
kubectl create namespace "$namespace" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl delete pod "$pod" -n "$namespace" --ignore-not-found --wait=true >/dev/null
kubectl apply -n "$namespace" -f - >/dev/null <<YAML
apiVersion: v1
kind: Pod
metadata:
  name: $pod
  labels: {app.kubernetes.io/name: aiops-demo, aiops/demo-failure: "$mode"}
spec:
  automountServiceAccountToken: false
  securityContext: {runAsNonRoot: true, runAsUser: 10001, seccompProfile: {type: RuntimeDefault}}
  containers:
    - name: app
      image: $image
      command: $command
      env: $env
      resources: {requests: {cpu: 10m, memory: 16Mi}, limits: {cpu: 50m, memory: 32Mi}}
      securityContext: {allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: {drop: [ALL]}}
YAML
echo "$pod"
