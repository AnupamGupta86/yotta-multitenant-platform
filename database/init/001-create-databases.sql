CREATE ROLE provisioning LOGIN PASSWORD 'provisioning-local-only';
CREATE DATABASE provisioning OWNER provisioning;

CREATE ROLE website_user1 LOGIN PASSWORD 'user1-local-only';
CREATE DATABASE website_user1 OWNER website_user1;

CREATE ROLE website_user2 LOGIN PASSWORD 'user2-local-only';
CREATE DATABASE website_user2 OWNER website_user2;

CREATE ROLE website_user3 LOGIN PASSWORD 'user3-local-only';
CREATE DATABASE website_user3 OWNER website_user3;
