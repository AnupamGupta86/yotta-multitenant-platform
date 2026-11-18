-- 001: extensions and shared helpers. Runs first; later scripts depend on both.

-- gen_random_uuid() for UUID primary keys
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Keeps updated_at current on every UPDATE (attached to tables by their own scripts)
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;
