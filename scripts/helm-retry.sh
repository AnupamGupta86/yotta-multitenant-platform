#!/usr/bin/env bash
# Provide a retry wrapper for transient Helm/network failures; source this file and call helm_retry <command> [args...].

DEFAULT_HELM_RETRY_ATTEMPTS=3
HELM_RETRY_DELAY_SECONDS=5
HELM_RETRY_ATTEMPTS="${HELM_RETRY_ATTEMPTS:-$DEFAULT_HELM_RETRY_ATTEMPTS}"

helm_retry() {
  local attempts="$HELM_RETRY_ATTEMPTS"
  local attempt delay

  for ((attempt = 1; attempt <= attempts; attempt++)); do
    if "$@"; then
      return 0
    fi

    if ((attempt == attempts)); then
      printf 'Command failed after %s attempts: %s\n' "$attempts" "$*" >&2
      return 1
    fi

    delay=$((attempt * HELM_RETRY_DELAY_SECONDS))
    printf 'Command failed (attempt %s/%s): %s; retrying in %ss.\n' \
      "$attempt" "$attempts" "$*" "$delay" >&2
    sleep "$delay"
  done
}
