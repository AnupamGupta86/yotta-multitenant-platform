#!/usr/bin/env bash
# Make sure a Docker engine is reachable and big enough for the Kind platform.
# Works with Colima (started here if it is installed and stopped) and with Docker Desktop.
# Source it from a setup script:  source "$script_dir/check-docker.sh"
# Settings (environment): COLIMA_CPU (default 4), COLIMA_MEMORY in GiB (default 8), COLIMA_DISK in GiB (default 60).
# Written for macOS's bash 3.2: one command per step, results kept in variables.

COLIMA_CPU="${COLIMA_CPU:-4}"
COLIMA_MEMORY="${COLIMA_MEMORY:-8}"
COLIMA_DISK="${COLIMA_DISK:-60}"
MIN_DOCKER_MEMORY_GIB=6
INOTIFY_MAX_USER_WATCHES=524288
INOTIFY_MAX_USER_INSTANCES=512

has_colima=false
colima_running=false
if command -v colima >/dev/null 2>&1; then
  has_colima=true
  if colima status >/dev/null 2>&1; then
    colima_running=true
  fi
fi

# 1. Colima installed but not running: start it with enough resources for the platform
if [ "$has_colima" = true ] && [ "$colima_running" = false ]; then
  colima_args="--cpu $COLIMA_CPU --memory $COLIMA_MEMORY"
  colima_profiles="$(colima list 2>/dev/null | awk 'NR > 1 { print $1 }')"
  if ! printf '%s\n' "$colima_profiles" | grep -qx default; then
    # The disk size can only be set when the VM is created (Colima cannot shrink it later)
    colima_args="$colima_args --disk $COLIMA_DISK"
  fi
  echo "Starting Colima ($colima_args)..."
  # shellcheck disable=SC2086 # the options are deliberately split into words
  colima start $colima_args
  colima_running=true
fi

# 2. Colima running but the docker CLI still points at another context (e.g. Docker Desktop's)
if [ "$colima_running" = true ] && [ -z "${DOCKER_HOST:-}" ]; then
  current_context="$(docker context show 2>/dev/null)"
  case "$current_context" in
    colima*) ;;
    *)
      if docker context inspect colima >/dev/null 2>&1; then
        echo "Switching the docker CLI to the Colima context (was: $current_context)"
        docker context use colima >/dev/null
      fi
      ;;
  esac
fi

# 3. Still no engine: explain the actual cause
if ! docker info >/dev/null 2>&1; then
  docker_problem="$(docker info 2>&1 >/dev/null | tail -n 3)"
  echo "Docker is not reachable: $docker_problem" >&2
  if [ "$has_colima" = true ]; then
    echo "Colima: run 'colima status' or 'colima start --cpu $COLIMA_CPU --memory $COLIMA_MEMORY', then 'docker context use colima'." >&2
  else
    echo "Start your Docker engine (Colima: 'brew install colima docker' then 'colima start'; or Docker Desktop)." >&2
  fi
  exit 1
fi

# 4. A Docker Desktop credential helper left in ~/.docker/config.json breaks pull/login without Desktop
docker_config="${DOCKER_CONFIG:-$HOME/.docker}/config.json"
if [ -f "$docker_config" ] && grep -q '"credsStore"[[:space:]]*:[[:space:]]*"desktop"' "$docker_config"; then
  if ! command -v docker-credential-desktop >/dev/null 2>&1; then
    echo "Warning: $docker_config sets credsStore to desktop, but Docker Desktop's helper is not installed." >&2
    echo "         docker pull/login will fail: change it to osxkeychain (brew install docker-credential-helper) or remove that line." >&2
  fi
fi

# 5. Enough memory for Kafka, Prometheus, Loki, Calico and the platform database?
docker_memory_bytes="$(docker info --format '{{.MemTotal}}' 2>/dev/null)"
case "$docker_memory_bytes" in
  '' | *[!0-9]*) docker_memory_bytes=0 ;;
esac
docker_memory_gib=$((docker_memory_bytes / 1024 / 1024 / 1024))
if [ "$docker_memory_gib" -gt 0 ] && [ "$docker_memory_gib" -lt "$MIN_DOCKER_MEMORY_GIB" ]; then
  echo "Warning: the Docker engine has $docker_memory_gib GiB of memory; the platform needs at least $MIN_DOCKER_MEMORY_GIB GiB." >&2
  if [ "$colima_running" = true ]; then
    echo "         Colima: colima stop; colima start --cpu $COLIMA_CPU --memory $COLIMA_MEMORY" >&2
  fi
fi

# 6. Colima's VM allows only 128 inotify instances: too few for a 2-node Kind cluster plus Fluentd
if [ "$colima_running" = true ]; then
  if ! colima ssh -- sudo sysctl -q -w "fs.inotify.max_user_watches=$INOTIFY_MAX_USER_WATCHES" \
    "fs.inotify.max_user_instances=$INOTIFY_MAX_USER_INSTANCES" >/dev/null 2>&1; then
    echo "Warning: could not raise the inotify limits in the Colima VM" >&2
  fi
fi
