-- 006: resource and autoscaling configuration, one row per website.
-- Defaults match the website chart (requests 50m/128Mi, limits 500m/256Mi, HPA 1-8, CPU 60 %, memory 75 %).
CREATE TABLE IF NOT EXISTS website_configs (
    config_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    website_id     UUID        NOT NULL REFERENCES websites (website_id) ON DELETE CASCADE,
    cpu_request    VARCHAR(30) NOT NULL DEFAULT '50m',
    cpu_limit      VARCHAR(30) NOT NULL DEFAULT '500m',
    memory_request VARCHAR(30) NOT NULL DEFAULT '128Mi',
    memory_limit   VARCHAR(30) NOT NULL DEFAULT '256Mi',
    min_replicas   INTEGER     NOT NULL DEFAULT 1,
    max_replicas   INTEGER     NOT NULL DEFAULT 8,
    cpu_target     INTEGER     NOT NULL DEFAULT 60,   -- HPA target, % of the CPU request
    memory_target  INTEGER     NOT NULL DEFAULT 75,   -- HPA target, % of the memory request
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_website_configs_website UNIQUE (website_id),
    CONSTRAINT chk_replica_counts CHECK (min_replicas >= 1 AND max_replicas >= min_replicas AND max_replicas <= 50),
    CONSTRAINT chk_cpu_target CHECK (cpu_target BETWEEN 1 AND 100),
    CONSTRAINT chk_memory_target CHECK (memory_target BETWEEN 1 AND 100),
    -- Kubernetes quantities: CPU as cores or millicores (0.5, 500m), memory with a binary unit (128Mi, 1Gi)
    CONSTRAINT chk_cpu_quantities CHECK (cpu_request ~ '^[0-9]+(\.[0-9]+)?m?$' AND cpu_limit ~ '^[0-9]+(\.[0-9]+)?m?$'),
    CONSTRAINT chk_memory_quantities CHECK (memory_request ~ '^[0-9]+(Ki|Mi|Gi)$' AND memory_limit ~ '^[0-9]+(Ki|Mi|Gi)$')
);

CREATE OR REPLACE TRIGGER trg_website_configs_updated_at
    BEFORE UPDATE ON website_configs FOR EACH ROW EXECUTE FUNCTION set_updated_at();
