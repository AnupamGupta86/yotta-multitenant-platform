#!/usr/bin/env bash
# Wait until the AIOps pod doctor has reported on one pod, then print the report
# (and add it to the GitHub job summary when run in Actions).
#
# Usage: aiops-pod-doctor/scripts/wait-for-report.sh <pod-namespace> <pod-name> [timeout-seconds]
# Needs: AIOPS_NAMESPACE, AIOPS_DEPLOYMENT (from .github/pipeline-config.env), kubectl, python3
set -euo pipefail

target_namespace="$1"
target_pod="$2"
timeout="${3:-180}"
reports_path="/api/v1/namespaces/$AIOPS_NAMESPACE/services/$AIOPS_DEPLOYMENT:http/proxy/reports"

deadline=$(( SECONDS + timeout ))
until (( SECONDS >= deadline )); do
  # The API server proxies the request to the doctor's /reports endpoint (no port-forward needed)
  if report="$(kubectl get --raw "$reports_path" 2>/dev/null | python3 -c '
import json, sys
namespace, pod = sys.argv[1], sys.argv[2]
for report in reversed(json.load(sys.stdin)):
    if report["namespace"] == namespace and report["pod"] == pod:
        print("## AIOps report: %s/%s\n" % (namespace, pod))
        print("| Failure | Container | Restarts | Analysed by |\n|---|---|---|---|")
        print("| %s | %s | %s | %s |\n" % (report["reason"], report["container"], report["restart_count"], report["analysed_by"]))
        print("```text\n%s\n```" % report["analysis"])
        sys.exit(0)
sys.exit(1)
' "$target_namespace" "$target_pod")"; then
    printf '%s\n' "$report"
    [[ -n "${GITHUB_STEP_SUMMARY:-}" ]] && printf '%s\n' "$report" >> "$GITHUB_STEP_SUMMARY"
    exit 0
  fi
  sleep 5
done

echo "::error title=No AIOps report::No report for $target_namespace/$target_pod after ${timeout}s"
kubectl logs deployment/"$AIOPS_DEPLOYMENT" -n "$AIOPS_NAMESPACE" --tail=50 || true
exit 1
