-- Schema test: run against a database initialized with init/*.sql.
--   psql -v ON_ERROR_STOP=1 -U <user> -d <db> -f test/schema_test.sql
-- Everything runs in one transaction that is rolled back, so the database is left unchanged.
-- Any failed expectation raises an exception, and psql exits non-zero.
\set ON_ERROR_STOP on
\set QUIET on
\o /dev/null
BEGIN;

-- Helper: the statement must fail with the given SQLSTATE (23505 unique, 23503 FK, 23514 check)
CREATE FUNCTION pg_temp.expect_error(stmt text, code text, what text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    EXECUTE stmt;
    RAISE EXCEPTION 'FAIL: % (statement succeeded, expected SQLSTATE %)', what, code;
EXCEPTION WHEN others THEN
    IF SQLSTATE <> code THEN
        RAISE EXCEPTION 'FAIL: % (got SQLSTATE % "%", expected %)', what, SQLSTATE, SQLERRM, code;
    END IF;
    RAISE NOTICE 'ok   %', what;
END $$;

-- 1. Structure
DO $$
DECLARE missing text;
BEGIN
    SELECT string_agg(t, ', ') INTO missing
    FROM unnest(ARRAY['users', 'websites', 'domains', 'deployments', 'website_configs', 'deployment_events']) AS t
    WHERE to_regclass('public.' || t) IS NULL;
    IF missing IS NOT NULL THEN RAISE EXCEPTION 'FAIL: missing tables: %', missing; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pgcrypto') THEN RAISE EXCEPTION 'FAIL: pgcrypto missing'; END IF;
    IF (SELECT count(*) FROM users) <> 0 THEN RAISE EXCEPTION 'FAIL: init scripts must not insert sample users'; END IF;
    IF (SELECT count(*) FROM pg_indexes WHERE schemaname = 'public' AND indexname LIKE 'idx\_%') < 7 THEN
        RAISE EXCEPTION 'FAIL: performance indexes missing';
    END IF;
    RAISE NOTICE 'ok   6 tables, pgcrypto, indexes, no sample users';
END $$;

-- 2. Valid data, with generated UUIDs and default timestamps
INSERT INTO users (username, email, password_hash) VALUES ('alice', 'alice@example.com', 'x');
INSERT INTO websites (user_id, website_name, namespace)
    SELECT user_id, 'Shop', 'user1' FROM users WHERE username = 'alice';
INSERT INTO domains (website_id, domain_name) SELECT website_id, 'user1.myyotta.com' FROM websites;
INSERT INTO website_configs (website_id) SELECT website_id FROM websites;
INSERT INTO deployments (website_id, image_name, image_tag, helm_revision, status)
    SELECT website_id, 'ghcr.io/x/website', '1.0.0-20261118000000', 1, 'SUCCEEDED' FROM websites;
INSERT INTO deployment_events (website_id, deployment_id, event_type)
    SELECT website_id, deployment_id, 'WebsiteCreated' FROM deployments;
DO $$
BEGIN
    IF (SELECT user_id IS NULL OR created_at IS NULL FROM users) THEN RAISE EXCEPTION 'FAIL: defaults'; END IF;
    IF (SELECT min_replicas <> 1 OR max_replicas <> 8 FROM website_configs) THEN RAISE EXCEPTION 'FAIL: config defaults'; END IF;
    RAISE NOTICE 'ok   valid rows insert with generated UUIDs and defaults';
END $$;

-- 3. Unique constraints
SELECT pg_temp.expect_error($q$INSERT INTO users (username, email, password_hash) VALUES ('bob', 'alice@example.com', 'x')$q$, '23505', 'duplicate email rejected');
SELECT pg_temp.expect_error($q$INSERT INTO users (username, email, password_hash) VALUES ('alice', 'other@example.com', 'x')$q$, '23505', 'duplicate username rejected');
SELECT pg_temp.expect_error($q$INSERT INTO websites (user_id, website_name, namespace) SELECT user_id, 'Shop', 'user9' FROM users$q$, '23505', 'duplicate website name per user rejected');
SELECT pg_temp.expect_error($q$INSERT INTO websites (user_id, website_name, namespace) SELECT user_id, 'Blog', 'user1' FROM users$q$, '23505', 'duplicate namespace rejected');
SELECT pg_temp.expect_error($q$INSERT INTO domains (website_id, domain_name) SELECT website_id, 'user1.myyotta.com' FROM websites$q$, '23505', 'duplicate domain rejected');
SELECT pg_temp.expect_error($q$INSERT INTO domains (website_id, domain_name) SELECT website_id, 'second.myyotta.com' FROM websites$q$, '23505', 'second PLATFORM domain for a website rejected');
SELECT pg_temp.expect_error($q$INSERT INTO website_configs (website_id) SELECT website_id FROM websites$q$, '23505', 'second config for a website rejected');

-- 4. Foreign keys
SELECT pg_temp.expect_error($q$INSERT INTO websites (user_id, website_name, namespace) VALUES (gen_random_uuid(), 'Ghost', 'ghost')$q$, '23503', 'website for an unknown user rejected');
SELECT pg_temp.expect_error($q$INSERT INTO deployments (website_id, image_name, image_tag) VALUES (gen_random_uuid(), 'i', 't')$q$, '23503', 'deployment for an unknown website rejected');

-- 5. CHECK constraints: replicas, CPU/memory targets and quantities, statuses, names
SELECT pg_temp.expect_error($q$UPDATE website_configs SET min_replicas = 0$q$, '23514', 'min_replicas 0 rejected');
SELECT pg_temp.expect_error($q$UPDATE website_configs SET min_replicas = 5, max_replicas = 2$q$, '23514', 'max_replicas < min_replicas rejected');
SELECT pg_temp.expect_error($q$UPDATE website_configs SET max_replicas = 51$q$, '23514', 'max_replicas above 50 rejected');
SELECT pg_temp.expect_error($q$UPDATE website_configs SET cpu_target = 0$q$, '23514', 'cpu_target 0 rejected');
SELECT pg_temp.expect_error($q$UPDATE website_configs SET memory_target = 101$q$, '23514', 'memory_target 101 rejected');
SELECT pg_temp.expect_error($q$UPDATE website_configs SET cpu_limit = '2 cores'$q$, '23514', 'invalid CPU quantity rejected');
SELECT pg_temp.expect_error($q$UPDATE website_configs SET memory_limit = '512MB'$q$, '23514', 'invalid memory quantity rejected');
SELECT pg_temp.expect_error($q$UPDATE websites SET status = 'RUNNING'$q$, '23514', 'unknown website status rejected');
SELECT pg_temp.expect_error($q$UPDATE websites SET namespace = 'User_1'$q$, '23514', 'invalid namespace rejected');
SELECT pg_temp.expect_error($q$INSERT INTO users (username, email, password_hash) VALUES ('carol', 'Carol@Example.com', 'x')$q$, '23514', 'mixed-case email rejected');
SELECT pg_temp.expect_error($q$UPDATE deployments SET helm_revision = 0$q$, '23514', 'helm revision 0 rejected');
SELECT pg_temp.expect_error($q$UPDATE deployments SET started_at = now(), completed_at = now() - interval '1 hour'$q$, '23514', 'completed before started rejected');

-- 6. updated_at trigger: an UPDATE must overwrite updated_at with the current (transaction) time
DO $$
BEGIN
    UPDATE users SET status = 'SUSPENDED', updated_at = '2000-01-01' WHERE username = 'alice';
    IF (SELECT updated_at FROM users WHERE username = 'alice') <> now() THEN RAISE EXCEPTION 'FAIL: updated_at not refreshed'; END IF;
    RAISE NOTICE 'ok   updated_at refreshed on UPDATE';
END $$;

-- 7. ON DELETE behaviour
SELECT pg_temp.expect_error($q$DELETE FROM users WHERE username = 'alice'$q$, '23503', 'user with websites cannot be deleted (RESTRICT)');
DO $$
BEGIN
    DELETE FROM deployments;
    IF (SELECT count(*) FROM deployment_events WHERE deployment_id IS NULL) <> 1 THEN
        RAISE EXCEPTION 'FAIL: deleting a deployment must keep its events (SET NULL)';
    END IF;
    DELETE FROM websites;
    IF (SELECT count(*) FROM domains) + (SELECT count(*) FROM website_configs) + (SELECT count(*) FROM deployment_events) <> 0 THEN
        RAISE EXCEPTION 'FAIL: deleting a website must remove its domains, config and events (CASCADE)';
    END IF;
    DELETE FROM users;
    RAISE NOTICE 'ok   ON DELETE: user RESTRICT, deployment events SET NULL, website children CASCADE';
END $$;

ROLLBACK;
\o
\echo 'Schema test passed'
