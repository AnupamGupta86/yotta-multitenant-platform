#!/usr/bin/env bash
# Create (or update) the GitHub environments the CD workflows deploy through, each with a
# required reviewer. Every CD job then pauses before its first step until a reviewer approves.
#
#   environment                       used by
#   platform-deployment               platform_cd.yml               (add-ons, platform chart, monitoring)
#   website-orchestrator-deployment   website_orchestrator_cd.yml
#   website-provisioning-deployment   website_provisioning_cd.yml
#   website-app-deployment            website_app_cd.yml            (one run per tenant website)
#   aiops-deployment                  aiops_pod_doctor_cd.yml       (also holds the GROK_API_KEY secret)
#
# Deployments are only allowed from master, feature and feature/* (the same branches the CD jobs accept).
# Safe to run again: it updates the environments in place.
#
# Usage: scripts/setup-deployment-approvals.sh [reviewer-github-login ...]
#        (defaults to the logged-in gh user; needs `gh auth login` with admin rights on the repo)
set -euo pipefail

command -v gh >/dev/null || { echo "Install the GitHub CLI first: https://cli.github.com" >&2; exit 1; }
repo="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
reviewers=("$@")
if (( ${#reviewers[@]} == 0 )); then
  reviewers=("$(gh api user --jq .login)")
fi

# GitHub identifies reviewers by numeric user id
reviewer_json=""
for login in "${reviewers[@]}"; do
  id="$(gh api "users/$login" --jq .id)"
  reviewer_json+="${reviewer_json:+,}{\"type\":\"User\",\"id\":$id}"
done

for environment in platform-deployment website-orchestrator-deployment \
                   website-provisioning-deployment website-app-deployment aiops-deployment; do
  # prevent_self_review=false: with a single maintainer, the person who starts a run must be
  # allowed to approve it, or nothing could ever be deployed
  gh api --method PUT "repos/$repo/environments/$environment" --input - >/dev/null <<JSON
{
  "reviewers": [$reviewer_json],
  "prevent_self_review": false,
  "wait_timer": 0,
  "deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}
}
JSON
  # Which branches may deploy (existing patterns are left as they are)
  for pattern in master feature "feature/*"; do
    gh api --method POST "repos/$repo/environments/$environment/deployment-branch-policies" \
      -f name="$pattern" -f type=branch >/dev/null 2>&1 || true
  done
  echo "✔ $environment: approval required from ${reviewers[*]}; deploys allowed from master, feature, feature/*"
done

echo
echo "Done. Each CD run now waits at 'Review deployments' until a reviewer approves it."
