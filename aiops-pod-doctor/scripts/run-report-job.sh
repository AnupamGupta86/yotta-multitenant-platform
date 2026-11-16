#!/usr/bin/env bash
# Start the scheduled e-mail report once, now (instead of waiting up to 5 minutes), wait for it
# and show its log. Used by the AIOps CD negative test; also handy by hand:
#
#   AIOPS_NAMESPACE=aiops AIOPS_REPORT_CRONJOB=aiops-pod-doctor-report \
#     aiops-pod-doctor/scripts/run-report-job.sh aiops-report-manual
#
# Fails when the run fails (for example, the e-mail could not be sent).
set -euo pipefail

job="${1:-aiops-report-manual-$(date +%s)}"
namespace="${AIOPS_NAMESPACE:-aiops}"
cronjob="${AIOPS_REPORT_CRONJOB:-aiops-pod-doctor-report}"
timeout="${2:-240}"

kubectl create job "$job" --from=cronjob/"$cronjob" --namespace "$namespace"

deadline=$(( SECONDS + timeout ))
result=""
until (( SECONDS >= deadline )); do
  succeeded="$(kubectl get job "$job" -n "$namespace" -o jsonpath='{.status.succeeded}')"
  failed="$(kubectl get job "$job" -n "$namespace" -o jsonpath='{.status.failed}')"
  if [[ "${succeeded:-0}" -ge 1 ]]; then result=succeeded; break; fi
  if [[ "${failed:-0}" -ge 1 ]]; then result=failed; break; fi
  sleep 5
done

log="$(kubectl logs job/"$job" -n "$namespace" --tail=200 --pod-running-timeout=30s 2>&1 || true)"
printf '%s\n' "$log"
if [[ "$result" != succeeded ]]; then
  # Show why the run did not finish: pod state, its events (image pull, volumes, scheduling) and the job
  echo "----- diagnostics -----"
  kubectl get pods -n "$namespace" -l job-name="$job" -o wide || true
  kubectl describe pods -n "$namespace" -l job-name="$job" | sed -n '/^Events:/,$p' || true
  kubectl describe job "$job" -n "$namespace" | sed -n '/^Events:/,$p' || true
fi
fence="$(printf '\140\140\140')"   # a Markdown code fence (three backticks)
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo "## Scheduled e-mail report (job $job): ${result:-timed out}"
    echo
    printf '%s\n' "${fence}text"
    printf '%s\n' "$log" | tail -60
    printf '%s\n' "$fence"
  } >> "$GITHUB_STEP_SUMMARY"
fi

if [[ "$result" != succeeded ]]; then
  echo "::error title=Report job ${result:-timed out}::See the job log above"
  exit 1
fi
