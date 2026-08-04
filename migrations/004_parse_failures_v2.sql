-- Migration 004: rename parse_failures.raw -> raw_message, add error_reason
-- Run once in Supabase SQL editor.

ALTER TABLE parse_failures RENAME COLUMN raw TO raw_message;

ALTER TABLE parse_failures
    ADD COLUMN IF NOT EXISTS error_reason TEXT NOT NULL DEFAULT '';
