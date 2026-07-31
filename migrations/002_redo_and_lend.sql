-- Migration 002: redo_state table + lend direction fix
-- Run this once in your Supabase SQL editor.

-- 1. Redo state — survives bot restarts (one row per user, upserted on /undo)
CREATE TABLE IF NOT EXISTS redo_state (
    user_id      UUID        PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    raw          TEXT        NOT NULL DEFAULT '',
    total_amount NUMERIC     NOT NULL DEFAULT 0,
    category_id  UUID        REFERENCES categories(id) ON DELETE SET NULL,
    saved_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. Lend fix — if your borrow_entries.direction column has a CHECK constraint
--    that only allows 'borrowed'/'repaid', run these two lines:
--
--    ALTER TABLE borrow_entries
--        DROP CONSTRAINT IF EXISTS borrow_entries_direction_check;
--
--    ALTER TABLE borrow_entries
--        ADD CONSTRAINT borrow_entries_direction_check
--        CHECK (direction IN ('borrowed', 'repaid', 'lent', 'lent_repaid'));
--
-- If /lend works after the code update, you don't need the lines above.
