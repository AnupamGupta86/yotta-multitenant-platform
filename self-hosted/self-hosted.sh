#!/usr/bin/env bash
set -euo pipefail
umask 077

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
runner_dir="$script_dir"

runner_version='2.338.2'
runner_archive="actions-runner-osx-arm64-${runner_version}.tar.gz"
runner_sha256='25a065af409ffa317ab4d5a3b614dd15fc9c40f2172bc1b35a1b1258e919a388'
runner_url="https://github.com/actions/runner/releases/download/v${runner_version}/${runner_archive}"
runner_repository_url="${RUNNER_REPOSITORY_URL:-https://github.com/AnupamGupta86/yotta-multitenant-platform}"
runner_settings_url="${runner_repository_url}/settings/actions/runners/new"
runner_name="${RUNNER_NAME:-$(hostname)}"
runner_labels="${RUNNER_LABELS:-yotta-local-k8s}"

# Reuse this directory when the runner package is already extracted here.
# Otherwise install into a dedicated subdirectory without failing if it exists.
if [[ ! -x "$runner_dir/config.sh" || ! -x "$runner_dir/run.sh" ]]; then
  runner_dir="$script_dir/actions-runner"
  mkdir -p "$runner_dir"
  if [[ ! -x "$runner_dir/config.sh" || ! -x "$runner_dir/run.sh" ]]; then
    cd "$runner_dir"
    if [[ ! -f "$runner_archive" ]]; then
      curl --fail --location --retry 3 --retry-delay 2 \
        --output "$runner_archive" "$runner_url"
    fi
    printf '%s  %s\n' "$runner_sha256" "$runner_archive" | shasum -a 256 --check -
    tar -xzf "$runner_archive"
  fi
fi

cd "$runner_dir"

if [[ -e .runner ]]; then
  echo "Runner is already configured; starting it."
elif [[ -e .credentials || -e .credentials_rsaparams ]]; then
  echo 'Incomplete runner configuration detected. Resolve or remove the stale runner registration before retrying.' >&2
  exit 2
else
  echo "Runner registration target: $runner_repository_url"
  echo "Generate a repository-level token at $runner_settings_url and use it within one hour."
  if [[ -z "${RUNNER_REGISTRATION_TOKEN:-}" ]]; then
    if [[ ! -t 0 ]]; then
      echo 'Set RUNNER_REGISTRATION_TOKEN to a fresh short-lived GitHub runner registration token.' >&2
      exit 2
    fi
    read -r -s -p 'Paste a fresh GitHub runner registration token: ' RUNNER_REGISTRATION_TOKEN
    printf '\n'
  fi
  if [[ -z "$RUNNER_REGISTRATION_TOKEN" ]]; then
    echo 'A runner registration token is required.' >&2
    exit 2
  fi

  ./config.sh --unattended \
    --url "$runner_repository_url" \
    --token "$RUNNER_REGISTRATION_TOKEN" \
    --name "$runner_name" \
    --labels "$runner_labels" \
    --work _work
  unset RUNNER_REGISTRATION_TOKEN
fi

exec ./run.sh
