FROM fluent/fluentd:v1.20.1-debian-1.0
USER 0
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends build-essential=12.12 \
    && gem install fluent-plugin-grafana-loki:1.3.0 fluent-plugin-kubernetes_metadata_filter:3.8.1 msgpack:1.8.7 --no-document \
    && gem uninstall msgpack -v 1.8.5 --executables --ignore-dependencies --force \
    && apt-get purge -y build-essential \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*
USER 1000:1000
