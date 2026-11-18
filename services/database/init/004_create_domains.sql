-- 004: domain mappings. A domain belongs to exactly one website.
CREATE TABLE IF NOT EXISTS domains (
    domain_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    website_id          UUID         NOT NULL REFERENCES websites (website_id) ON DELETE CASCADE,
    domain_name         VARCHAR(253) NOT NULL,
    domain_type         VARCHAR(20)  NOT NULL DEFAULT 'PLATFORM',
    verification_status VARCHAR(30)  NOT NULL DEFAULT 'PENDING',
    tls_status          VARCHAR(30)  NOT NULL DEFAULT 'PENDING',
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_domains_name UNIQUE (domain_name),
    CONSTRAINT chk_domains_name CHECK (domain_name = lower(domain_name) AND domain_name ~ '^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)+$'),
    CONSTRAINT chk_domains_type CHECK (domain_type IN ('PLATFORM', 'CUSTOM')),
    CONSTRAINT chk_domains_verification CHECK (verification_status IN ('PENDING', 'VERIFIED', 'FAILED')),
    CONSTRAINT chk_domains_tls CHECK (tls_status IN ('PENDING', 'ISSUED', 'FAILED', 'EXPIRED'))
);
