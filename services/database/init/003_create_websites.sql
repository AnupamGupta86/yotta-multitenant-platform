-- 003: websites owned by users. One Kubernetes namespace per website.
CREATE TABLE IF NOT EXISTS websites (
    website_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- RESTRICT: an account that still owns websites cannot be deleted; deprovision its websites first,
    -- so their deployment history is never removed as a side effect of deleting a user
    user_id        UUID         NOT NULL REFERENCES users (user_id) ON DELETE RESTRICT,
    website_name   VARCHAR(150) NOT NULL,
    namespace      VARCHAR(63)  NOT NULL,
    repository_url TEXT,
    branch         VARCHAR(100) NOT NULL DEFAULT 'main',
    status         VARCHAR(30)  NOT NULL DEFAULT 'PENDING',
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_websites_user_name UNIQUE (user_id, website_name),
    CONSTRAINT uq_websites_namespace UNIQUE (namespace),
    -- a valid Kubernetes namespace name (DNS label)
    CONSTRAINT chk_websites_namespace CHECK (namespace ~ '^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$'),
    CONSTRAINT chk_websites_status CHECK (status IN ('PENDING', 'PROVISIONING', 'ACTIVE', 'FAILED', 'SUSPENDED', 'DELETING'))
);

CREATE OR REPLACE TRIGGER trg_websites_updated_at
    BEFORE UPDATE ON websites FOR EACH ROW EXECUTE FUNCTION set_updated_at();
