# PostgreSQL 16 for the myyotta platform, with the schema built in.
# On the first start with an empty data directory, the official entrypoint runs
# /docker-entrypoint-initdb.d/*.sql in name order (001 ... 008). Later starts reuse the data and skip them.
FROM postgres:16

# Build-time only: apt must not prompt (no terminal during docker build)
ARG DEBIAN_FRONTEND=noninteractive
# Patch OS packages. gosu is only used by the entrypoint to drop from root to postgres; this image never runs
# as root (USER 999 below), so remove it: less attack surface, and its old Go runtime trips image scanners.
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/* /usr/local/bin/gosu

# Defaults from the platform naming list; the deployment can override them, and must supply
# POSTGRES_PASSWORD (from a Kubernetes Secret), so no credential is ever baked into the image.
ENV POSTGRES_DB=myyotta_platform \
    POSTGRES_USER=myyotta_app \
    PGDATA=/var/lib/postgresql/data/pgdata

COPY init/ /docker-entrypoint-initdb.d/
# Copied files keep the build machine's permissions: make them readable by the postgres user
RUN chmod 0755 /docker-entrypoint-initdb.d && chmod 0444 /docker-entrypoint-initdb.d/*.sql

# Run as the image's postgres user (uid 999), never as root
USER 999:999
EXPOSE 5432
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=5 \
  CMD ["sh", "-c", "pg_isready -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\""]
