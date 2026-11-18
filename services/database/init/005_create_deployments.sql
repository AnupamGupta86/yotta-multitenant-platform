-- 005: deployment history of each website (one row per Helm install/upgrade/rollback).
CREATE TABLE IF NOT EXISTS deployments (
    deployment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    website_id    UUID         NOT NULL REFERENCES websites (website_id) ON DELETE CASCADE,
    image_name    TEXT         NOT NULL,
    image_tag     VARCHAR(150) NOT NULL,
    commit_sha    VARCHAR(64),
    helm_revision INTEGER,
    status        VARCHAR(30)  NOT NULL DEFAULT 'PENDING',
    started_at    TIMESTAMPTZ,
    completed_at  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT chk_deployments_status CHECK (status IN ('PENDING', 'IN_PROGRESS', 'SUCCEEDED', 'FAILED', 'ROLLED_BACK')),
    CONSTRAINT chk_deployments_helm_revision CHECK (helm_revision IS NULL OR helm_revision > 0),
    CONSTRAINT chk_deployments_times CHECK (completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at)
);
