-- 008: indexes for the foreign keys and the frequent queries.
-- (Primary keys and UNIQUE constraints already have their own indexes, e.g. website_configs.website_id.)

-- Foreign keys: joins, and fast ON DELETE checks on the parent tables
CREATE INDEX IF NOT EXISTS idx_websites_user_id ON websites (user_id);
CREATE INDEX IF NOT EXISTS idx_domains_website_id ON domains (website_id);
CREATE INDEX IF NOT EXISTS idx_deployment_events_deployment_id ON deployment_events (deployment_id);

-- "Latest deployments / events of a website": the FK index and the sort in one
CREATE INDEX IF NOT EXISTS idx_deployments_website_created ON deployments (website_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_deployment_events_website_created ON deployment_events (website_id, created_at DESC);

-- Dashboards and workers: websites by status, events still waiting to be published to Kafka
CREATE INDEX IF NOT EXISTS idx_websites_status ON websites (status);
CREATE INDEX IF NOT EXISTS idx_deployment_events_pending ON deployment_events (created_at)
    WHERE published_status = 'PENDING';

-- At most one platform-issued domain per website (custom domains are unlimited)
CREATE UNIQUE INDEX IF NOT EXISTS uq_domains_one_platform_domain ON domains (website_id)
    WHERE domain_type = 'PLATFORM';
