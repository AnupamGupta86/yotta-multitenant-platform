#!/usr/bin/env bash
# End-to-end test of the Hosting Platform API with its real dependencies, as containers on one Docker network:
# the platform database image (services/database: schema 001-008), Kafka, and the API image.
# Calls every endpoint and checks the HTTP status, the Kafka events and the database rows.
# Usage: .github/scripts/provisioning-e2e.sh <api-image> <database-image>
# Needs (pipeline-config.env): KAFKA_CI_IMAGE, KAFKA_INTERNAL_PORT, DATABASE_NAME, DATABASE_USER
set -euo pipefail

api_image="${1:?usage: provisioning-e2e.sh <api-image> <database-image>}"
db_image="${2:?database image is required}"
network=provisioning-e2e
db_password=ci-only-not-secret
failures=0

cleanup() {
  if ((failures)); then
    for c in e2e-api e2e-db e2e-kafka; do echo "--- logs $c"; docker logs --tail 40 "$c" 2>&1 || true; done
  fi
  docker rm -f e2e-api e2e-db e2e-kafka >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create "$network" >/dev/null
docker run -d --name e2e-db --network "$network" -e POSTGRES_PASSWORD="$db_password" "$db_image" >/dev/null
docker run -d --name e2e-kafka --network "$network" \
  -e KAFKA_NODE_ID=1 -e KAFKA_PROCESS_ROLES=broker,controller \
  -e "KAFKA_LISTENERS=CONTROLLER://:9093,INTERNAL://:$KAFKA_INTERNAL_PORT" \
  -e "KAFKA_ADVERTISED_LISTENERS=INTERNAL://e2e-kafka:$KAFKA_INTERNAL_PORT" \
  -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=CONTROLLER:PLAINTEXT,INTERNAL:PLAINTEXT \
  -e KAFKA_CONTROLLER_LISTENER_NAMES=CONTROLLER -e KAFKA_CONTROLLER_QUORUM_VOTERS=1@e2e-kafka:9093 \
  -e KAFKA_INTER_BROKER_LISTENER_NAME=INTERNAL -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 \
  -e KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR=1 -e KAFKA_TRANSACTION_STATE_LOG_MIN_ISR=1 \
  -e KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS=0 "$KAFKA_CI_IMAGE" >/dev/null

for _ in $(seq 1 60); do   # TCP ready = the init scripts have finished (they run on a socket-only server)
  docker exec e2e-db pg_isready -h 127.0.0.1 -U "$DATABASE_USER" -d "$DATABASE_NAME" >/dev/null 2>&1 && break
  sleep 2
done
for _ in $(seq 1 45); do
  docker exec e2e-kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server "localhost:$KAFKA_INTERNAL_PORT" --list >/dev/null 2>&1 && break
  sleep 2
done
docker exec e2e-kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server "localhost:$KAFKA_INTERNAL_PORT" \
  --create --if-not-exists --topic website-events --partitions 1 --replication-factor 1 >/dev/null

docker run -d --name e2e-api --network "$network" -p 127.0.0.1:3000:3000 -e HOST=0.0.0.0 \
  -e DB_HOST=e2e-db -e DB_PORT=5432 -e DB_NAME="$DATABASE_NAME" -e DB_USER="$DATABASE_USER" -e DB_PASSWORD="$db_password" \
  -e "KAFKA_BOOTSTRAP_SERVERS=e2e-kafka:$KAFKA_INTERNAL_PORT" "$api_image" >/dev/null
for _ in $(seq 1 30); do
  curl -fsS http://127.0.0.1:3000/health/ready >/dev/null 2>&1 && break
  sleep 2
done

# expect <description> <expected HTTP status> <curl arguments...>: prints the body, counts failures
expect() {
  local what="$1" want="$2"; shift 2
  local body status
  body="$(curl -sS -o /tmp/e2e-body -w '%{http_code}' "$@" || true)"
  status="$body"; body="$(cat /tmp/e2e-body 2>/dev/null || true)"
  if [ "$status" = "$want" ]; then
    printf 'ok   %-58s %s\n' "$what" "$status"
  else
    printf 'FAIL %-58s got %s, expected %s: %s\n' "$what" "$status" "$want" "$body"
    failures=$((failures + 1))
  fi
}
api=http://127.0.0.1:3000
json=(-H 'Content-Type: application/json')

expect "GET /health/live"                                    200 "$api/health/live"
expect "GET /health/ready (database)"                        200 "$api/health/ready"
expect "GET /api/v1/health (database + Kafka)"               200 "$api/api/v1/health"
expect "POST /api/v1/users/register"                         201 -X POST "${json[@]}" "$api/api/v1/users/register" -d '{"username":"alice","email":"alice@example.com","password":"correct-horse-1"}'
expect "POST /api/v1/users/register again (duplicate)"       409 -X POST "${json[@]}" "$api/api/v1/users/register" -d '{"username":"alice","email":"alice@example.com","password":"correct-horse-1"}'
expect "POST /api/v1/users/register weak password"           422 -X POST "${json[@]}" "$api/api/v1/users/register" -d '{"username":"bob","email":"bob@example.com","password":"short"}'
expect "POST /api/v1/auth/login does not exist"              404 -X POST "${json[@]}" "$api/api/v1/auth/login" -d '{}'
expect "POST /api/v1/websites (unregistered owner)"          404 -X POST "${json[@]}" "$api/api/v1/websites" -d '{"tenant":"user7","domain":"user7.myyotta.com","email":"nobody@example.com"}'
expect "POST /api/v1/websites"                               202 -X POST "${json[@]}" "$api/api/v1/websites" -d '{"tenant":"user7","domain":"user7.myyotta.com","email":"alice@example.com"}'
expect "POST /api/v1/websites again (idempotent)"            202 -X POST "${json[@]}" "$api/api/v1/websites" -d '{"tenant":"user7","domain":"user7.myyotta.com","email":"alice@example.com"}'
expect "POST /api/v1/websites (domain already used)"         409 -X POST "${json[@]}" "$api/api/v1/websites" -d '{"tenant":"user8","domain":"user7.myyotta.com","email":"alice@example.com"}'
expect "GET /api/v1/users"                                   200 "$api/api/v1/users"
grep -q '"email":"alice@example.com"' /tmp/e2e-body || { echo "FAIL GET /api/v1/users does not list alice"; failures=$((failures + 1)); }
if grep -qi 'password\|scrypt' /tmp/e2e-body; then echo "FAIL GET /api/v1/users exposes password data"; failures=$((failures + 1)); fi
expect "GET /api/v1/websites"                                200 "$api/api/v1/websites"
grep -q '"tenant":"user7"' /tmp/e2e-body || { echo "FAIL GET /api/v1/websites does not list user7"; failures=$((failures + 1)); }
expect "GET /api/v1/websites?email=alice@example.com"        200 "$api/api/v1/websites?email=alice@example.com"
expect "DELETE /api/v1/websites?tenant=user7"                202 -X DELETE "$api/api/v1/websites?tenant=user7"
expect "DELETE /api/v1/websites?tenant=nosuch"               404 -X DELETE "$api/api/v1/websites?tenant=nosuch"

echo "--- platform database rows"
docker exec e2e-db psql -U "$DATABASE_USER" -d "$DATABASE_NAME" -At -c \
  "SELECT 'website', namespace, status FROM websites UNION ALL SELECT 'event', event_type, published_status FROM deployment_events ORDER BY 1, 2"
rows="$(docker exec e2e-db psql -U "$DATABASE_USER" -d "$DATABASE_NAME" -At -c \
  "SELECT (SELECT status FROM websites WHERE namespace='user7') || ' ' || (SELECT count(*) FROM deployment_events WHERE published_status='PUBLISHED')")"
[ "$rows" = "DELETING 2" ] || { echo "FAIL expected website DELETING and 2 published events, got: $rows"; failures=$((failures + 1)); }

echo "--- Kafka topic website-events"
events="$(docker exec e2e-kafka /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server "localhost:$KAFKA_INTERNAL_PORT" \
  --topic website-events --from-beginning --timeout-ms 10000 2>/dev/null || true)"
echo "$events"
for type in WebsiteProvisioningRequested WebsiteDeletionRequested; do
  [ "$(grep -c "\"event_type\":\"$type\"" <<<"$events" || true)" = 1 ] \
    || { echo "FAIL expected exactly one $type event on Kafka"; failures=$((failures + 1)); }
done

if ((failures)); then
  echo "Provisioning end-to-end test: $failures failure(s)" >&2
  exit 1
fi
echo "Provisioning end-to-end test passed"
