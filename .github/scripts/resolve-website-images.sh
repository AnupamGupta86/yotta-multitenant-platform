#!/usr/bin/env bash
# Decide which images a tenant deployment uses, and export them as IMAGE and ORCHESTRATOR_IMAGE.
#
#   website-app image (IMAGE_TAG)
#     given  -> that tag (checked against the CI format)
#     empty  -> the newest published version tag of website-app in GHCR
#   website-orchestrator image (ORCHESTRATOR_IMAGE_TAG; runs the job that publishes WebsiteCreated)
#     given  -> that tag
#     empty  -> exactly the image the running website-orchestrator deployment uses, so a tenant
#               deployment never changes or depends on a different orchestrator version
#
# Needs: CONTAINER_REGISTRY, GITHUB_REPOSITORY_OWNER, WEBSITE_APP_IMAGE, WEBSITE_ORCHESTRATOR_IMAGE,
#        PLATFORM_NAMESPACE, WEBSITE_ORCHESTRATOR_DEPLOYMENT, GHCR_TOKEN; kubectl, curl, python3.
set -euo pipefail

owner="$(printf '%s' "$GITHUB_REPOSITORY_OWNER" | tr '[:upper:]' '[:lower:]')"
validate="$(dirname "$0")/validate-image-tag.sh"
TAG_PATTERN='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-[0-9]{14}$'

# ---- website-app
if [[ -n "${IMAGE_TAG:-}" ]]; then
  bash "$validate" "$IMAGE_TAG"
  app_tag="$IMAGE_TAG"
  app_source="requested"
else
  # GHCR's registry API takes the GitHub token base64-encoded as a bearer token
  token="$(printf '%s' "$GHCR_TOKEN" | base64 | tr -d '\n')"
  app_tag="$(curl -fsSL -H "Authorization: Bearer $token" \
      "https://$CONTAINER_REGISTRY/v2/$owner/$WEBSITE_APP_IMAGE/tags/list?n=1000" \
    | python3 -c 'import json,sys; print("\n".join(json.load(sys.stdin).get("tags") or []))' \
    | grep -E "$TAG_PATTERN" | sort -V | tail -n 1 || true)"
  if [[ -z "$app_tag" ]]; then
    echo "::error::No published $WEBSITE_APP_IMAGE image with a version tag was found. Run Website App CI, or pass image_tag."
    exit 1
  fi
  app_source="newest published"
fi

# ---- website-orchestrator
if [[ -n "${ORCHESTRATOR_IMAGE_TAG:-}" ]]; then
  bash "$validate" "$ORCHESTRATOR_IMAGE_TAG"
  orchestrator_image="$CONTAINER_REGISTRY/$owner/$WEBSITE_ORCHESTRATOR_IMAGE:$ORCHESTRATOR_IMAGE_TAG"
  orchestrator_source="requested"
else
  orchestrator_image="$(kubectl get deployment "$WEBSITE_ORCHESTRATOR_DEPLOYMENT" --namespace "$PLATFORM_NAMESPACE" \
    -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)"
  if [[ -z "$orchestrator_image" ]]; then
    echo "::error::The $WEBSITE_ORCHESTRATOR_DEPLOYMENT deployment isn't running in '$PLATFORM_NAMESPACE'. Deploy it first (Website Orchestrator Deployment), or pass orchestrator_image_tag."
    exit 1
  fi
  orchestrator_source="currently deployed"
fi

image="$CONTAINER_REGISTRY/$owner/$WEBSITE_APP_IMAGE:$app_tag"
{
  echo "IMAGE=$image"
  echo "ORCHESTRATOR_IMAGE=$orchestrator_image"
} >> "${GITHUB_ENV:-/dev/stdout}"
{
  echo "### Images for this deployment"
  echo "- website-app: \`$image\` ($app_source)"
  echo "- website-orchestrator: \`$orchestrator_image\` ($orchestrator_source)"
} | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
