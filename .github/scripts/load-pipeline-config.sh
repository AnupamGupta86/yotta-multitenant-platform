#!/usr/bin/env bash
# Load shared non-secret GitHub Actions defaults into GITHUB_ENV and step outputs.
set -euo pipefail

CONFIG_FILE="${GITHUB_WORKSPACE:-.}/.github/pipeline-config.env"
[[ -r "$CONFIG_FILE" ]] || { echo "Missing pipeline config: $CONFIG_FILE" >&2; exit 2; }
[[ -n "${GITHUB_ENV:-}" && -n "${GITHUB_OUTPUT:-}" ]] || {
  echo 'GITHUB_ENV and GITHUB_OUTPUT must be set by a GitHub Actions runner.' >&2
  exit 2
}

while IFS= read -r line || [[ -n "$line" ]]; do
  [[ -z "$line" || "$line" == \#* ]] && continue
  [[ "$line" == *=* ]] || { echo "Invalid config entry: $line" >&2; exit 2; }

  key="${line%%=*}"
  value="${line#*=}"
  value="${value%$'\r'}"
  [[ "$key" =~ ^[A-Z][A-Z0-9_]*$ ]] || { echo "Invalid config key: $key" >&2; exit 2; }
  [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] || {
    echo "Multiline config values are not supported: $key" >&2
    exit 2
  }

  output_key="$(printf '%s' "$key" | tr '[:upper:]' '[:lower:]')"
  printf '%s=%s\n' "$key" "$value" >> "$GITHUB_ENV"
  printf '%s=%s\n' "$output_key" "$value" >> "$GITHUB_OUTPUT"
done < "$CONFIG_FILE"
