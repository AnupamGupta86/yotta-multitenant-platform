#!/usr/bin/env bash
# Validate one Helm chart before anything is deployed: helm lint, helm template, then kubeconform
# (strict Kubernetes schema check). The same checks as CI, run again by every CD on the commit it deploys.
# Usage: .github/scripts/validate-chart.sh <release> <chart> [--namespace <ns>] [--set/--set-string/--values ...]
# Needs helm and kubeconform on PATH. The rendered manifests are written to $RENDER_DIR (default /tmp/rendered).
set -euo pipefail

release="${1:?usage: validate-chart.sh <release> <chart> [--namespace <ns>] [values options...]}"
chart="${2:?chart is required}"
shift 2
RENDER_DIR="${RENDER_DIR:-/tmp/rendered}"
mkdir -p "$RENDER_DIR"
rendered="$RENDER_DIR/$release.yaml"

template_args=("$@")
lint_args=()   # helm lint takes the values options, not --namespace
for ((i = 0; i < ${#template_args[@]}; i++)); do
  case "${template_args[i]}" in
    --namespace | -n) i=$((i + 1)) ;;
    --namespace=*) ;;
    *) lint_args+=("${template_args[i]}") ;;
  esac
done

echo "::group::helm lint $chart"
helm lint "$chart" ${lint_args[@]+"${lint_args[@]}"}
echo "::endgroup::"

helm template "$release" "$chart" ${template_args[@]+"${template_args[@]}"} >"$rendered"
echo "Rendered $(grep -c '^kind:' "$rendered") resources from $chart -> $rendered"

kubeconform -strict -ignore-missing-schemas -summary "$rendered"
