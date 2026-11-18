-- 007: website lifecycle events (the same events the platform publishes to Kafka).
CREATE TABLE IF NOT EXISTS deployment_events (
    event_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- SET NULL: removing one deployment keeps the website's event history
    deployment_id    UUID         REFERENCES deployments (deployment_id) ON DELETE SET NULL,
    website_id       UUID         NOT NULL REFERENCES websites (website_id) ON DELETE CASCADE,
    event_type       VARCHAR(100) NOT NULL,
    kafka_topic      VARCHAR(255) NOT NULL DEFAULT 'website-events',
    published_status VARCHAR(30)  NOT NULL DEFAULT 'PENDING',
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT chk_deployment_events_type CHECK (event_type ~ '^[A-Z][A-Za-z]+$'),   -- e.g. WebsiteCreated
    CONSTRAINT chk_deployment_events_published CHECK (published_status IN ('PENDING', 'PUBLISHED', 'FAILED'))
);
