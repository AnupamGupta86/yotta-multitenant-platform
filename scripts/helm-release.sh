#!/usr/bin/env bash
# Install/upgrade one Helm release, or show what that upgrade would change.
# Usage: scripts/helm-release.sh <release> <chart> [helm upgrade options...]
#   HELM_MODE=apply (default)  helm upgrade --install <release> <chart> <options>
#   HELM_MODE=diff             helm diff upgrade with the same options against the live cluster:
#                              coloured in the job log (green added, red removed, yellow per resource),
#                              a diff block in the run summary, and a file in $HELM_DIFF_DIR.
# Callers pass identical options in both modes, so the reviewed diff is exactly what the deploy applies.
set -euo pipefail

HELM_DIFF_PLUGIN_URL=https://github.com/databus23/helm-diff
HELM_DIFF_VERSION="${HELM_DIFF_VERSION:-}"          # empty = latest release of the plugin
HELM_DIFF_CONTEXT_LINES=5
SUMMARY_MAX_LINES=1500                              # the run summary is limited to 1 MiB per step
HELM_DIFF_DIR="${HELM_DIFF_DIR:-${RUNNER_TEMP:-/tmp}/helm-diff}"
GREEN=$'\033[32m'; RED=$'\033[31m'; YELLOW=$'\033[1;33m'; RESET=$'\033[0m'

release="${1:?usage: helm-release.sh <release> <chart> [helm upgrade options...]}"
chart="${2:?chart is required}"
shift 2
mode="${HELM_MODE:-apply}"

if [[ "$mode" == apply ]]; then
  exec helm upgrade --install "$release" "$chart" "$@"
fi
[[ "$mode" == diff ]] || { echo "HELM_MODE must be apply or diff (got: $mode)" >&2; exit 2; }

ensure_helm_diff() {
  helm plugin list 2>/dev/null | awk 'NR > 1 {print $1}' | grep -qx diff && return 0
  local version_args=()
  [[ -n "$HELM_DIFF_VERSION" ]] && version_args=(--version "$HELM_DIFF_VERSION")
  echo "Installing the helm-diff plugin ${HELM_DIFF_VERSION:-(latest)}"
  if ! helm plugin install "$HELM_DIFF_PLUGIN_URL" "${version_args[@]+"${version_args[@]}"}"; then
    # Helm 4 refuses plugins without a provenance file unless verification is turned off.
    echo "::warning::helm-diff has no signature for this Helm version; installing it with --verify=false"
    helm plugin install "$HELM_DIFF_PLUGIN_URL" "${version_args[@]+"${version_args[@]}"}" --verify=false
  fi
}

# Keep the options that change the rendered manifests; drop the ones that only control how the upgrade runs.
diff_args=()
namespace=default
while (($#)); do
  case "$1" in
    --wait | --wait=* | --create-namespace | --atomic | --rollback-on-failure | --take-ownership | \
      --force-conflicts | --cleanup-on-fail | --wait-for-jobs | --server-side=*) ;;
    --timeout | --server-side) shift ;;
    --timeout=*) ;;
    --namespace | -n) namespace="$2"; diff_args+=("$1" "$2"); shift ;;
    --namespace=*) namespace="${1#*=}"; diff_args+=("$1") ;;
    *) diff_args+=("$1") ;;
  esac
  shift
done

ensure_helm_diff
mkdir -p "$HELM_DIFF_DIR"
diff_file="$HELM_DIFF_DIR/$namespace-$release.diff"

status=0
helm diff upgrade "$release" "$chart" --allow-unreleased --no-color --detailed-exitcode \
  --context "$HELM_DIFF_CONTEXT_LINES" "${diff_args[@]+"${diff_args[@]}"}" >"$diff_file" || status=$?
# --detailed-exitcode: 0 = no changes, 2 = changes, anything else = error
if ((status != 0 && status != 2)); then
  cat "$diff_file" >&2
  echo "::error::helm diff failed for release $release in namespace $namespace"
  exit "$status"
fi

new_release=false
helm status "$release" --namespace "$namespace" >/dev/null 2>&1 || new_release=true
added="$(grep -c 'has been added:$' "$diff_file" || true)"
removed="$(grep -c 'has been removed:$' "$diff_file" || true)"
changed="$(grep -c 'has changed:$' "$diff_file" || true)"

if [[ "$new_release" == true ]]; then
  headline="$release ($namespace): NEW release, $added resources will be created"
elif ((status == 0)); then
  headline="$release ($namespace): no changes"
else
  headline="$release ($namespace): $changed changed, $added added, $removed removed"
fi

# Job log: coloured diff
echo "${YELLOW}==================== $headline ====================${RESET}"
if ((status == 2)); then
  awk -v g="$GREEN" -v r="$RED" -v y="$YELLOW" -v z="$RESET" '
    /has (been added|been removed|changed):$/ { print y $0 z; next }
    /^\+/ { print g $0 z; next }
    /^-/  { print r $0 z; next }
    { print }' "$diff_file"
fi
echo "::notice title=Helm changes::$headline"

# Run summary: one collapsible diff per release
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo "#### $headline"
    if ((status == 2)); then
      echo "<details><summary>Show the diff</summary>"
      echo
      echo '```diff'
      head -n "$SUMMARY_MAX_LINES" "$diff_file"
      echo '```'
      if (($(wc -l <"$diff_file") > SUMMARY_MAX_LINES)); then
        echo "_Truncated after $SUMMARY_MAX_LINES lines: download the helm-diff artifact for the full diff._"
      fi
      echo "</details>"
    fi
    echo
  } >>"$GITHUB_STEP_SUMMARY"
fi
