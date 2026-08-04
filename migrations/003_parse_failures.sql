-- Migration 003: parse_failures table
-- Run once in Supabase SQL editor.

CREATE TABLE IF NOT EXISTS parse_failures (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID        REFERENCES users(id) ON DELETE SET NULL,
    raw        TEXT        NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS parse_failures_created_at_idx ON parse_failures (created_at);
