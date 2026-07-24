-- Daily Spend — Supabase schema
-- Run this once in the Supabase SQL editor to set up all tables.
-- Service-role key bypasses RLS, so no policies needed for MVP.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── Users ────────────────────────────────────────────────────────────────────
CREATE TABLE users (
  id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  telegram_id     BIGINT      UNIQUE NOT NULL,
  display_name    TEXT,
  currency        TEXT        NOT NULL DEFAULT 'TRY',
  timezone        TEXT        NOT NULL DEFAULT 'Europe/Istanbul',
  weekly_digest   BOOLEAN     NOT NULL DEFAULT FALSE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Categories ───────────────────────────────────────────────────────────────
-- Seeded per-user on /start; user_id is always set (no shared nulls in MVP).
CREATE TABLE categories (
  id          UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID    NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name        TEXT    NOT NULL,
  is_default  BOOLEAN NOT NULL DEFAULT FALSE,
  UNIQUE(user_id, name)
);

-- ── Items ────────────────────────────────────────────────────────────────────
-- Named entities that make price-history queries possible.
-- canonical_name is normalised by the Claude parser ("loaf of bread" → "bread").
CREATE TABLE items (
  id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  canonical_name  TEXT        NOT NULL,
  category_id     UUID        REFERENCES categories(id),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(user_id, canonical_name)
);

-- ── Expense entries ──────────────────────────────────────────────────────────
-- One row per incoming message (even if it contains multiple items).
CREATE TABLE expense_entries (
  id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  raw_message   TEXT,
  category_id   UUID        REFERENCES categories(id),
  total_amount  NUMERIC(12,2) NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Line items ───────────────────────────────────────────────────────────────
-- One row per item within a message — this is the table price-history runs on.
-- Query: SELECT amount, created_at FROM line_items WHERE item_id = ? ORDER BY created_at
CREATE TABLE line_items (
  id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  expense_entry_id  UUID        NOT NULL REFERENCES expense_entries(id) ON DELETE CASCADE,
  item_id           UUID        NOT NULL REFERENCES items(id),
  amount            NUMERIC(12,2) NOT NULL,
  quantity          NUMERIC(10,2),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Gave entries ─────────────────────────────────────────────────────────────
-- "gave pedro 500" — hangs off an expense_entry so /undo works uniformly.
CREATE TABLE gave_entries (
  id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  expense_entry_id  UUID        NOT NULL REFERENCES expense_entries(id) ON DELETE CASCADE,
  recipient_name    TEXT        NOT NULL,
  amount            NUMERIC(12,2) NOT NULL
);

-- ── Borrow ledger ────────────────────────────────────────────────────────────
-- Separate from expenses; direction 'borrowed'|'repaid' tracks running balance.
CREATE TABLE borrow_entries (
  id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id             UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  counterparty_name   TEXT        NOT NULL,
  amount              NUMERIC(12,2) NOT NULL,
  direction           TEXT        NOT NULL CHECK (direction IN ('borrowed', 'repaid')),
  note                TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Income ───────────────────────────────────────────────────────────────────
CREATE TABLE income (
  id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  amount      NUMERIC(12,2) NOT NULL,
  source      TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Indexes ──────────────────────────────────────────────────────────────────
CREATE INDEX idx_users_telegram_id         ON users(telegram_id);
CREATE INDEX idx_expense_entries_user_date ON expense_entries(user_id, created_at DESC);
CREATE INDEX idx_line_items_item_date      ON line_items(item_id, created_at DESC);
CREATE INDEX idx_items_user_name           ON items(user_id, canonical_name);
CREATE INDEX idx_borrow_user_party         ON borrow_entries(user_id, counterparty_name);
CREATE INDEX idx_gave_entry                ON gave_entries(expense_entry_id);
